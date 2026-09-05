from __future__ import annotations

import os
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from app.contracts import handoff_contract, validate_task_input, validate_task_result
from app.workflow_graph import WORKFLOW_STATES, resolve_workflow_transition


router = APIRouter(prefix="/taskhub", tags=["taskhub"])

TASK_STATES = {"pending", "running", "succeeded", "failed", "blocked", "canceled"}
PIPELINE_STATES = {"active", "paused", "completed", "canceled"}
ALLOWED_TRANSITIONS = {
    ("pending", "running"),
    ("pending", "blocked"),
    ("running", "succeeded"),
    ("running", "failed"),
    ("running", "blocked"),
    ("failed", "pending"),
    ("blocked", "pending"),
    ("pending", "canceled"),
    ("failed", "canceled"),
    ("blocked", "canceled"),
}
SENSITIVE_KEYS = (
    "API_KEY",
    "ACCESS_KEY",
    "SECRET_KEY",
    "PRIVATE_KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "COOKIE",
    "AUTH",
    "SESSION",
)
APPROVAL_DOWNSTREAM_TYPES = {
    "code.change",
    "code.change.apply",
    "code.diff.preview",
    "ops.note",
    "project.context.sync",
    "project.git.status",
    "quality.env.check",
    "workspace.bootstrap",
    "requirement.split",
    "test.run",
    "h5.inspect",
}
IMPLEMENTATION_TASK_TYPES = {"code.change", "code.diff.preview", "code.change.apply"}
WORKSPACE_TASK_TYPES = IMPLEMENTATION_TASK_TYPES | {"test.run", "quality.env.check", "workspace.bootstrap"}
AUTO_REWORK_FAILURE_TYPES = {"test.run", "quality.env.check"}
AUTO_REWORK_POLICY_VERSION = 2
QUALITY_TASK_TYPES = {"review.model"}
GUI_TASK_TYPES = {"h5.inspect"}
UNSCHEDULABLE_TASK_TYPES = {"market.price.collect", "commerce.listing.draft"}


class TaskCreate(BaseModel):
    project: str = Field("douyin-listing-workbench", min_length=1)
    type: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    priority: int = 50
    input: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)
    depends_on: list[uuid.UUID] = Field(default_factory=list)
    pipeline_id: uuid.UUID | None = None
    workspace_id: str | None = Field(default=None, max_length=200)
    target_worker_id: str | None = Field(default=None, max_length=200)
    resource_keys: list[str] = Field(default_factory=list, max_length=20)


class TaskClaim(BaseModel):
    worker_id: str = Field(..., min_length=1)
    project: str = Field("douyin-listing-workbench", min_length=1)
    types: list[str] = Field(default_factory=list)


class PipelineCreate(BaseModel):
    project: str = Field("douyin-listing-workbench", min_length=1)
    name: str = Field(..., min_length=1, max_length=120)
    workspace_id: str = Field(..., min_length=1, max_length=200)
    default_worker_id: str | None = Field(default=None, max_length=200)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PipelineUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    state: str | None = None
    workspace_id: str | None = Field(default=None, min_length=1, max_length=200)
    default_worker_id: str | None = Field(default=None, max_length=200)
    metadata: dict[str, Any] | None = None


class TaskHeartbeat(BaseModel):
    worker_id: str = Field(..., min_length=1)
    lease_token: str = Field(..., min_length=1)


class TaskComplete(BaseModel):
    worker_id: str = Field(..., min_length=1)
    lease_token: str = Field(..., min_length=1)
    result: dict[str, Any] = Field(default_factory=dict)


class TaskFail(BaseModel):
    worker_id: str = Field(..., min_length=1)
    lease_token: str = Field(..., min_length=1)
    error: dict[str, Any] = Field(default_factory=dict)


class TaskAction(BaseModel):
    reason: str = Field(..., min_length=1)


class TaskRetry(TaskAction):
    input: dict[str, Any] | None = None


class TaskApprove(BaseModel):
    reason: str = Field(..., min_length=1)
    result: dict[str, Any] = Field(default_factory=dict)
    approval_plan: list[dict[str, Any]] | None = None
    approval_hash: str = Field(..., min_length=64, max_length=64)


class WorkflowAction(BaseModel):
    action: str = Field(..., min_length=1, max_length=40)
    reason: str = Field(..., min_length=1, max_length=2000)
    output: dict[str, Any] = Field(default_factory=dict)


class WorkflowBackfill(BaseModel):
    reason: str = Field(..., min_length=1, max_length=2000)


def database_url() -> str:
    value = os.getenv("DATABASE_URL")
    if not value:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not configured")
    return value


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def lease_duration() -> timedelta:
    return timedelta(seconds=max(30, int(os.getenv("TASKHUB_LEASE_SECONDS", "120"))))


def normalize_resource_keys(keys: list[str], workspace_id: str | None = None) -> list[str]:
    normalized = {str(key).strip() for key in keys if str(key).strip()}
    if workspace_id:
        normalized.add(f"workspace:{workspace_id.strip()}")
    if any(len(key) > 240 for key in normalized):
        raise HTTPException(status_code=400, detail="resource key is too long")
    if len(normalized) > 20:
        raise HTTPException(status_code=400, detail="too many resource keys")
    return sorted(normalized)


def default_target_worker(task_type: str) -> str | None:
    if task_type in WORKSPACE_TASK_TYPES:
        return os.getenv("TASKHUB_IMPLEMENTATION_WORKER", "worker-31-31-implementation").strip() or None
    if task_type in QUALITY_TASK_TYPES:
        return os.getenv("TASKHUB_QUALITY_WORKER", "worker-31-24-quality").strip() or None
    if task_type in GUI_TASK_TYPES:
        return os.getenv("TASKHUB_GUI_WORKER", "worker-31-34-gui").strip() or None
    return None


def worker_pool(task_type: str) -> list[str]:
    if task_type in WORKSPACE_TASK_TYPES:
        value = os.getenv(
            "TASKHUB_IMPLEMENTATION_WORKERS",
            "worker-31-31-implementation-a,worker-31-31-implementation",
        )
    elif task_type in QUALITY_TASK_TYPES:
        value = os.getenv("TASKHUB_QUALITY_WORKERS", "worker-31-24-quality")
    elif task_type in GUI_TASK_TYPES:
        value = os.getenv("TASKHUB_GUI_WORKERS", "worker-31-34-gui")
    else:
        return []
    return list(dict.fromkeys(item.strip() for item in value.split(",") if item.strip()))


def least_loaded_worker(cur, task_type: str) -> str | None:
    candidates = worker_pool(task_type)
    if not candidates:
        return default_target_worker(task_type)
    cur.execute(
        """select candidate.worker_id,
        count(task.id) filter (where task.state='running') running_count,
        count(task.id) filter (where task.state='pending') pending_count
        from unnest(%s::text[]) with ordinality candidate(worker_id, preference)
        left join taskhub_tasks task on task.target_worker_id=candidate.worker_id
          and task.state in ('pending','running')
        group by candidate.worker_id,candidate.preference
        order by running_count, pending_count, candidate.preference limit 1""",
        (candidates,),
    )
    row = cur.fetchone()
    return row["worker_id"] if row else default_target_worker(task_type)


def automatic_target_worker(cur, task_type: str, pipeline_worker_id: str | None) -> str | None:
    if task_type in QUALITY_TASK_TYPES or task_type in GUI_TASK_TYPES:
        return least_loaded_worker(cur, task_type)
    if pipeline_worker_id and task_type in WORKSPACE_TASK_TYPES:
        return pipeline_worker_id
    return pipeline_worker_id or least_loaded_worker(cur, task_type)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if any(marker in key.upper() for marker in SENSITIVE_KEYS):
                result[key] = "***REDACTED***"
            else:
                result[key] = redact(item)
        return result
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def serialize_task(row: dict[str, Any], include_lease_token: bool = False) -> dict[str, Any]:
    result = dict(row)
    if not include_lease_token:
        result.pop("lease_token", None)
    for key in ("input", "metadata", "result", "error"):
        result[key] = redact(result.get(key))
    return result


def serialize_history(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["payload"] = redact(result.get("payload"))
    return result


def connect():
    return psycopg.connect(database_url(), row_factory=dict_row)


def init_taskhub() -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                create table if not exists taskhub_tasks (
                    id uuid primary key,
                    project text not null,
                    type text not null,
                    title text not null,
                    state text not null check (state in ('pending', 'running', 'succeeded', 'failed', 'blocked', 'canceled')),
                    priority integer not null default 50,
                    input jsonb not null default '{}'::jsonb,
                    metadata jsonb not null default '{}'::jsonb,
                    result jsonb,
                    error jsonb,
                    worker_id text,
                    retry_count integer not null default 0,
                    created_at timestamptz not null,
                    updated_at timestamptz not null,
                    claimed_at timestamptz,
                    completed_at timestamptz
                )
                """
            )
            for statement in (
                "alter table taskhub_tasks add column if not exists idempotency_key text",
                "alter table taskhub_tasks add column if not exists lease_token uuid",
                "alter table taskhub_tasks add column if not exists lease_expires_at timestamptz",
                "alter table taskhub_tasks add column if not exists heartbeat_at timestamptz",
                "alter table taskhub_tasks add column if not exists approval_content_hash text",
                "alter table taskhub_tasks add column if not exists pipeline_id uuid",
                "alter table taskhub_tasks add column if not exists workspace_id text",
                "alter table taskhub_tasks add column if not exists target_worker_id text",
                "alter table taskhub_tasks add column if not exists resource_keys text[] not null default '{}'::text[]",
            ):
                cur.execute(statement)
            cur.execute(
                """
                create table if not exists taskhub_pipelines (
                    id uuid primary key,
                    project text not null,
                    name text not null,
                    state text not null check (state in ('active', 'paused', 'completed', 'canceled')),
                    workspace_id text not null,
                    default_worker_id text,
                    metadata jsonb not null default '{}'::jsonb,
                    created_at timestamptz not null,
                    updated_at timestamptz not null,
                    unique (project, name),
                    unique (project, workspace_id)
                )
                """
            )
            cur.execute(
                """
                create table if not exists taskhub_resource_leases (
                    resource_key text primary key,
                    task_id uuid references taskhub_tasks(id) on delete set null,
                    pipeline_id uuid,
                    worker_id text,
                    lease_token uuid,
                    expires_at timestamptz,
                    created_at timestamptz not null,
                    updated_at timestamptz not null
                )
                """
            )
            cur.execute(
                """
                create table if not exists taskhub_task_history (
                    id uuid primary key,
                    task_id uuid not null references taskhub_tasks(id) on delete cascade,
                    from_state text,
                    to_state text not null,
                    actor text not null,
                    reason text not null,
                    payload jsonb not null default '{}'::jsonb,
                    created_at timestamptz not null
                )
                """
            )
            cur.execute(
                """
                create table if not exists taskhub_task_dependencies (
                    task_id uuid not null references taskhub_tasks(id) on delete cascade,
                    depends_on_task_id uuid not null references taskhub_tasks(id) on delete cascade,
                    created_at timestamptz not null,
                    primary key (task_id, depends_on_task_id),
                    check (task_id <> depends_on_task_id)
                )
                """
            )
            cur.execute("create index if not exists idx_taskhub_tasks_queue on taskhub_tasks (state, project, priority desc, created_at)")
            cur.execute("create index if not exists idx_taskhub_tasks_updated on taskhub_tasks (updated_at desc)")
            cur.execute("create index if not exists idx_taskhub_history_task on taskhub_task_history (task_id, created_at desc)")
            cur.execute("create index if not exists idx_taskhub_dependencies_parent on taskhub_task_dependencies (depends_on_task_id)")
            cur.execute("create index if not exists idx_taskhub_tasks_pipeline on taskhub_tasks (pipeline_id, state, priority desc, created_at)")
            cur.execute("create index if not exists idx_taskhub_tasks_target_worker on taskhub_tasks (target_worker_id, state)")
            cur.execute("create index if not exists idx_taskhub_resource_task on taskhub_resource_leases (task_id) where task_id is not null")
            cur.execute(
                "create unique index if not exists idx_taskhub_idempotency on taskhub_tasks (project, idempotency_key) where idempotency_key is not null"
            )
            cur.execute(
                """
                create table if not exists taskhub_audit_events (
                    id uuid primary key,
                    actor text not null,
                    action text not null,
                    status_code integer not null,
                    client_ip text,
                    detail jsonb not null default '{}'::jsonb,
                    created_at timestamptz not null
                )
                """
            )
            cur.execute(
                """
                create table if not exists taskhub_workflows (
                    id uuid primary key,
                    project text not null,
                    requirement_hash text not null,
                    state text not null,
                    summary text not null,
                    risk_level text not null,
                    evidence_root_hash text not null,
                    created_at timestamptz not null,
                    updated_at timestamptz not null
                )
                """
            )
            for statement in (
                "alter table taskhub_workflows add column if not exists pipeline_id uuid",
                "alter table taskhub_workflows add column if not exists active_role text",
                "alter table taskhub_workflows add column if not exists resume_state text",
                "alter table taskhub_workflows add column if not exists iteration integer not null default 0",
                "alter table taskhub_workflows add column if not exists max_iterations integer not null default 3",
                "alter table taskhub_workflows add column if not exists context jsonb not null default '{}'::jsonb",
            ):
                cur.execute(statement)
            cur.execute(
                """
                create table if not exists taskhub_role_evidence (
                    id uuid primary key,
                    workflow_id uuid not null references taskhub_workflows(id) on delete cascade,
                    role text not null,
                    ordinal integer not null,
                    provider text not null,
                    model text not null,
                    status text not null,
                    input_hash text not null,
                    output_hash text not null,
                    chain_hash text not null,
                    payload jsonb not null default '{}'::jsonb,
                    created_at timestamptz not null,
                    unique (workflow_id, role)
                )
                """
            )
            cur.execute(
                """
                create table if not exists taskhub_workflow_transitions (
                    id uuid primary key,
                    workflow_id uuid not null references taskhub_workflows(id) on delete cascade,
                    from_state text,
                    to_state text not null,
                    action text not null,
                    actor text not null,
                    reason text not null,
                    role text,
                    iteration integer not null,
                    payload jsonb not null default '{}'::jsonb,
                    created_at timestamptz not null
                )
                """
            )
            cur.execute(
                """
                create table if not exists taskhub_workflow_role_runs (
                    id uuid primary key,
                    workflow_id uuid not null references taskhub_workflows(id) on delete cascade,
                    entry_transition_id uuid not null references taskhub_workflow_transitions(id) on delete cascade,
                    state text not null,
                    role text not null,
                    iteration integer not null,
                    status text not null,
                    attempt_count integer not null default 1,
                    provider text,
                    model text,
                    verdict text,
                    summary text,
                    output jsonb not null default '{}'::jsonb,
                    error jsonb not null default '{}'::jsonb,
                    started_at timestamptz not null,
                    completed_at timestamptz,
                    next_retry_at timestamptz,
                    unique (workflow_id, entry_transition_id)
                )
                """
            )
            cur.execute(
                """
                create table if not exists taskhub_alert_acknowledgements (
                    fingerprint text primary key,
                    acknowledged_by text not null,
                    acknowledged_at timestamptz not null
                )
                """
            )
            cur.execute(
                """
                create table if not exists taskhub_alert_deliveries (
                    id uuid primary key,
                    fingerprint text not null,
                    channel text not null,
                    severity text not null,
                    alert jsonb not null default '{}'::jsonb,
                    status text not null check (status in ('pending', 'delivered', 'failed')),
                    attempt_count integer not null default 0,
                    last_error text,
                    response_status integer,
                    next_retry_at timestamptz not null,
                    attempted_at timestamptz,
                    delivered_at timestamptz,
                    created_at timestamptz not null,
                    updated_at timestamptz not null,
                    unique (fingerprint, channel)
                )
                """
            )
            cur.execute(
                """
                create table if not exists taskhub_task_handoffs (
                    id uuid primary key,
                    task_id uuid not null unique references taskhub_tasks(id) on delete cascade,
                    workflow_id uuid,
                    contract_version text not null,
                    producer text not null,
                    consumer text not null,
                    status text not null check (status in ('pending', 'validated', 'rejected')),
                    contract jsonb not null default '{}'::jsonb,
                    payload jsonb not null default '{}'::jsonb,
                    payload_hash text,
                    validation_errors jsonb not null default '[]'::jsonb,
                    created_at timestamptz not null,
                    updated_at timestamptz not null
                )
                """
            )
            cur.execute(
                """
                create table if not exists taskhub_remediation_actions (
                    id uuid primary key,
                    fingerprint text not null,
                    alert_code text not null,
                    entity_type text not null,
                    entity_id text not null,
                    action text not null,
                    mode text not null,
                    status text not null check (status in ('proposed', 'executed', 'skipped', 'failed')),
                    detail jsonb not null default '{}'::jsonb,
                    created_at timestamptz not null,
                    completed_at timestamptz
                )
                """
            )
            cur.execute("create index if not exists idx_taskhub_audit_created on taskhub_audit_events (created_at desc)")
            cur.execute("create index if not exists idx_taskhub_workflows_project on taskhub_workflows (project, created_at desc)")
            cur.execute("create index if not exists idx_taskhub_evidence_workflow on taskhub_role_evidence (workflow_id, ordinal)")
            cur.execute("create index if not exists idx_taskhub_workflow_transitions on taskhub_workflow_transitions (workflow_id, created_at)")
            cur.execute(
                "create index if not exists idx_taskhub_role_runs_queue "
                "on taskhub_workflow_role_runs (status, next_retry_at, started_at)"
            )
            cur.execute(
                "create index if not exists idx_taskhub_alert_deliveries_queue "
                "on taskhub_alert_deliveries (status, next_retry_at, created_at)"
            )
            cur.execute(
                "create index if not exists idx_taskhub_remediation_recent "
                "on taskhub_remediation_actions (fingerprint, action, created_at desc)"
            )
            cur.execute(
                """
                update taskhub_workflows workflow
                set state = case
                        when exists (
                            select 1 from taskhub_tasks task
                            where task.metadata->>'workflow_id' = workflow.id::text
                              and task.type = 'review.human' and task.state = 'canceled'
                        ) then 'canceled'
                        when exists (
                            select 1 from taskhub_tasks task
                            where task.metadata->>'workflow_id' = workflow.id::text
                              and task.type = 'review.human' and task.state = 'blocked'
                        ) then 'blocked'
                        when exists (
                            select 1 from taskhub_tasks task
                            where task.metadata->>'workflow_id' = workflow.id::text
                              and task.type = 'review.human' and task.state = 'succeeded'
                        ) and not exists (
                            select 1 from taskhub_tasks task
                            where task.metadata->>'workflow_id' = workflow.id::text
                              and task.metadata ? 'approval_source_task_id'
                              and task.state <> 'succeeded'
                        ) then 'review'
                        when exists (
                            select 1 from taskhub_tasks task
                            where task.metadata->>'workflow_id' = workflow.id::text
                              and task.type = 'review.human' and task.state = 'succeeded'
                        ) then 'implementation'
                        else 'awaiting_plan_approval'
                    end,
                    active_role = case
                        when exists (
                            select 1 from taskhub_tasks task
                            where task.metadata->>'workflow_id' = workflow.id::text
                              and task.type = 'review.human' and task.state = 'succeeded'
                        ) and not exists (
                            select 1 from taskhub_tasks task
                            where task.metadata->>'workflow_id' = workflow.id::text
                              and task.metadata ? 'approval_source_task_id'
                              and task.state <> 'succeeded'
                        ) then 'reviewer'
                        when exists (
                            select 1 from taskhub_tasks task
                            where task.metadata->>'workflow_id' = workflow.id::text
                              and task.type = 'review.human' and task.state = 'succeeded'
                        ) then 'coder'
                        else 'supervisor'
                    end,
                    resume_state = case
                        when exists (
                            select 1 from taskhub_tasks task
                            where task.metadata->>'workflow_id' = workflow.id::text
                              and task.type = 'review.human' and task.state = 'blocked'
                        ) then 'awaiting_plan_approval'
                        else null
                    end
                where workflow.state = 'awaiting_human_approval'
                """
            )
            cur.execute(
                """
                select workflow.*
                from taskhub_workflows workflow
                where not exists (
                    select 1 from taskhub_workflow_transitions transition
                    where transition.workflow_id = workflow.id
                )
                """
            )
            for workflow in cur.fetchall():
                cur.execute(
                    """
                    insert into taskhub_workflow_transitions
                        (id, workflow_id, from_state, to_state, action, actor, reason,
                         role, iteration, payload, created_at)
                    values (%s, %s, null, %s, 'legacy_migrated', 'taskhub:migration',
                            'legacy workflow state inferred from existing tasks', %s, %s, %s, %s)
                    """,
                    (
                        uuid.uuid4(), workflow["id"], workflow["state"], workflow.get("active_role"),
                        workflow["iteration"], Jsonb({"inferred": True}), now_utc(),
                    ),
                )
            cur.execute(
                "select id, input from taskhub_tasks where type = 'review.human' and approval_content_hash is null"
            )
            for review_task in cur.fetchall():
                plan = (review_task.get("input") or {}).get("approval_plan", [])
                cur.execute(
                    "update taskhub_tasks set approval_content_hash = %s where id = %s",
                    (canonical_hash(plan), review_task["id"]),
                )
        conn.commit()


def record_audit_event(
    actor: str,
    action: str,
    status_code: int,
    client_ip: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into taskhub_audit_events (id, actor, action, status_code, client_ip, detail, created_at)
                    values (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (uuid.uuid4(), actor, action, status_code, client_ip, Jsonb(redact(detail or {})), now_utc()),
                )
            conn.commit()
    except Exception:
        # Auditing must not make the control plane unavailable.
        return


def create_planning_workflow(
    project: str,
    requirement: str,
    pipeline_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    workflow_id = uuid.uuid4()
    created_at = now_utc()
    requirement_hash = canonical_hash({"project": project, "requirement": requirement})
    summary = requirement.strip()[:160]
    with connect() as conn:
        with conn.cursor() as cur:
            if pipeline_id:
                cur.execute("select project, state from taskhub_pipelines where id = %s", (pipeline_id,))
                pipeline = cur.fetchone()
                if not pipeline:
                    raise HTTPException(status_code=400, detail="pipeline does not exist")
                if pipeline["project"] != project:
                    raise HTTPException(status_code=400, detail="pipeline belongs to a different project")
                if pipeline["state"] in {"completed", "canceled"}:
                    raise HTTPException(status_code=409, detail=f"pipeline is {pipeline['state']}")
            cur.execute(
                """
                insert into taskhub_workflows
                    (id, project, requirement_hash, state, summary, risk_level, evidence_root_hash,
                     pipeline_id, active_role, context, created_at, updated_at)
                values (%s, %s, %s, 'planning', %s, 'medium', %s, %s, 'planner', %s, %s, %s)
                """,
                (
                    workflow_id,
                    project,
                    requirement_hash,
                    summary,
                    requirement_hash,
                    pipeline_id,
                    Jsonb({"requirement": requirement, "planner_status": "queued"}),
                    created_at,
                    created_at,
                ),
            )
            cur.execute(
                """
                insert into taskhub_workflow_transitions
                    (id, workflow_id, from_state, to_state, action, actor, reason, role,
                     iteration, payload, created_at)
                values (%s, %s, null, 'planning', 'requirement_submitted', 'taskhub:web',
                        'requirement accepted for asynchronous planning', 'planner', 0, %s, %s)
                """,
                (uuid.uuid4(), workflow_id, Jsonb({"requirement_hash": requirement_hash}), created_at),
            )
        conn.commit()
    return {
        "workflow_id": str(workflow_id),
        "requirement_hash": requirement_hash,
        "state": "planning",
    }


def create_workflow_evidence(
    project: str,
    requirement: str,
    plan: dict[str, Any],
    pipeline_id: uuid.UUID | None = None,
    workflow_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    existing_workflow = workflow_id is not None
    workflow_id = workflow_id or uuid.uuid4()
    created_at = now_utc()
    requirement_hash = canonical_hash({"project": project, "requirement": requirement})
    previous_hash = requirement_hash
    evidence_rows = []
    for ordinal, output in enumerate(plan.get("role_outputs") or [], start=1):
        payload = redact(output)
        output_hash = canonical_hash(payload)
        chain_hash = canonical_hash(
            {
                "workflow_id": str(workflow_id),
                "ordinal": ordinal,
                "role": output.get("role"),
                "input_hash": previous_hash,
                "output_hash": output_hash,
            }
        )
        evidence_rows.append(
            {
                "id": uuid.uuid4(),
                "workflow_id": workflow_id,
                "role": str(output.get("role") or "unknown"),
                "ordinal": ordinal,
                "provider": str(output.get("source") or "fallback"),
                "model": str(output.get("model") or "unknown"),
                "status": str(output.get("status") or "unknown"),
                "input_hash": previous_hash,
                "output_hash": output_hash,
                "chain_hash": chain_hash,
                "payload": payload,
                "created_at": created_at,
            }
        )
        previous_hash = chain_hash

    with connect() as conn:
        with conn.cursor() as cur:
            if existing_workflow:
                cur.execute("select * from taskhub_workflows where id = %s for update", (workflow_id,))
                workflow = cur.fetchone()
                if not workflow:
                    raise HTTPException(status_code=404, detail="workflow not found")
                if workflow["project"] != project or workflow["requirement_hash"] != requirement_hash:
                    raise HTTPException(status_code=409, detail="workflow requirement does not match")
                if workflow["state"] != "planning":
                    raise HTTPException(status_code=409, detail=f"workflow is {workflow['state']}")
                context = {
                    **(workflow.get("context") or {}),
                    "planner_status": "completed",
                    "planner_source": plan.get("source"),
                    "planner_model": plan.get("model"),
                }
                cur.execute(
                    """
                    update taskhub_workflows
                    set state = 'awaiting_plan_approval', summary = %s, risk_level = %s,
                        evidence_root_hash = %s, active_role = 'supervisor', context = %s,
                        updated_at = %s
                    where id = %s
                    """,
                    (
                        str(plan.get("summary") or requirement),
                        str(plan.get("risk_level") or "medium"),
                        previous_hash,
                        Jsonb(context),
                        created_at,
                        workflow_id,
                    ),
                )
                transition_from = "planning"
                transition_actor = "workflow:planner"
                transition_reason = "planner completed; plan is ready for human approval"
            else:
                cur.execute(
                    """
                    insert into taskhub_workflows
                        (id, project, requirement_hash, state, summary, risk_level, evidence_root_hash,
                         pipeline_id, active_role, context, created_at, updated_at)
                    values (%s, %s, %s, 'awaiting_plan_approval', %s, %s, %s, %s, 'supervisor', %s, %s, %s)
                    """,
                    (
                        workflow_id,
                        project,
                        requirement_hash,
                        str(plan.get("summary") or ""),
                        str(plan.get("risk_level") or "medium"),
                        previous_hash,
                        pipeline_id,
                        Jsonb({
                            "requirement": requirement,
                            "planner_source": plan.get("source"),
                            "planner_model": plan.get("model"),
                        }),
                        created_at,
                        created_at,
                    ),
                )
                transition_from = None
                transition_actor = "multi_role_planner"
                transition_reason = "plan and evidence created"
            cur.execute(
                """
                insert into taskhub_workflow_transitions
                    (id, workflow_id, from_state, to_state, action, actor, reason, role, iteration, payload, created_at)
                values (%s, %s, %s, 'awaiting_plan_approval', 'plan_ready', %s, %s,
                        'supervisor', 0, %s, %s)
                """,
                (
                    uuid.uuid4(), workflow_id, transition_from, transition_actor, transition_reason,
                    Jsonb({"evidence_root_hash": previous_hash}), created_at,
                ),
            )
            for row in evidence_rows:
                cur.execute(
                    """
                    insert into taskhub_role_evidence
                        (id, workflow_id, role, ordinal, provider, model, status, input_hash,
                         output_hash, chain_hash, payload, created_at)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        row["id"], row["workflow_id"], row["role"], row["ordinal"],
                        row["provider"], row["model"], row["status"], row["input_hash"],
                        row["output_hash"], row["chain_hash"], Jsonb(row["payload"]), row["created_at"],
                    ),
                )
        conn.commit()
    return {
        "workflow_id": str(workflow_id),
        "requirement_hash": requirement_hash,
        "evidence_root_hash": previous_hash,
        "roles": [
            {
                "role": row["role"],
                "ordinal": row["ordinal"],
                "provider": row["provider"],
                "model": row["model"],
                "status": row["status"],
                "input_hash": row["input_hash"],
                "output_hash": row["output_hash"],
                "chain_hash": row["chain_hash"],
            }
            for row in evidence_rows
        ],
    }


@router.post("/pipelines")
def create_pipeline(request: Request, payload: PipelineCreate) -> dict[str, Any]:
    pipeline_id = uuid.uuid4()
    created_at = now_utc()
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into taskhub_pipelines
                        (id, project, name, state, workspace_id, default_worker_id, metadata, created_at, updated_at)
                    values (%s, %s, %s, 'active', %s, %s, %s, %s, %s)
                    returning *
                    """,
                    (
                        pipeline_id,
                        payload.project,
                        payload.name.strip(),
                        payload.workspace_id.strip(),
                        payload.default_worker_id.strip() if payload.default_worker_id else None,
                        Jsonb(redact(payload.metadata)),
                        created_at,
                        created_at,
                    ),
                )
                row = cur.fetchone()
            conn.commit()
    except psycopg.errors.UniqueViolation as exc:
        raise HTTPException(status_code=409, detail="pipeline name or workspace already exists") from exc
    record_audit_event(
        request.state.actor,
        "pipeline.create",
        201,
        detail={"pipeline_id": str(pipeline_id), "project": payload.project},
    )
    return row


@router.get("/pipelines")
def list_pipelines(
    project: str | None = None,
    state: str | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> list[dict[str, Any]]:
    filters: list[str] = []
    params: list[Any] = []
    if project:
        filters.append("pipeline.project = %s")
        params.append(project)
    if state:
        if state not in PIPELINE_STATES:
            raise HTTPException(status_code=400, detail="invalid pipeline state")
        filters.append("pipeline.state = %s")
        params.append(state)
    where = f"where {' and '.join(filters)}" if filters else ""
    params.append(limit)
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                select pipeline.*,
                       count(task.id) as task_count,
                       count(task.id) filter (where task.state = 'pending') as pending_count,
                       count(task.id) filter (where task.state = 'running') as running_count,
                       count(task.id) filter (where task.state = 'failed') as failed_count,
                       count(task.id) filter (where task.state = 'blocked') as blocked_count,
                       count(task.id) filter (where task.state = 'succeeded') as succeeded_count
                from taskhub_pipelines pipeline
                left join taskhub_tasks task on task.pipeline_id = pipeline.id
                {where}
                group by pipeline.id
                order by pipeline.updated_at desc
                limit %s
                """,
                params,
            )
            return cur.fetchall()


@router.get("/pipelines/{pipeline_id}")
def get_pipeline(pipeline_id: uuid.UUID) -> dict[str, Any]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("select * from taskhub_pipelines where id = %s", (pipeline_id,))
            pipeline = cur.fetchone()
            if not pipeline:
                raise HTTPException(status_code=404, detail="pipeline not found")
            cur.execute(
                "select * from taskhub_tasks where pipeline_id = %s order by created_at",
                (pipeline_id,),
            )
            tasks = cur.fetchall()
    return {**pipeline, "tasks": [serialize_task(task) for task in tasks]}


@router.patch("/pipelines/{pipeline_id}")
def update_pipeline(pipeline_id: uuid.UUID, request: Request, payload: PipelineUpdate) -> dict[str, Any]:
    if payload.state is not None and payload.state not in PIPELINE_STATES:
        raise HTTPException(status_code=400, detail="invalid pipeline state")
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="no pipeline fields supplied")
    assignments: list[str] = []
    params: list[Any] = []
    for field in ("name", "state", "workspace_id", "default_worker_id"):
        if field in updates:
            value = updates[field]
            if isinstance(value, str):
                value = value.strip() or None
            assignments.append(f"{field} = %s")
            params.append(value)
    if "metadata" in updates:
        assignments.append("metadata = %s")
        params.append(Jsonb(redact(updates["metadata"] or {})))
    assignments.append("updated_at = %s")
    params.append(now_utc())
    params.append(pipeline_id)
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"update taskhub_pipelines set {', '.join(assignments)} where id = %s returning *",
                    params,
                )
                row = cur.fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail="pipeline not found")
            conn.commit()
    except psycopg.errors.UniqueViolation as exc:
        raise HTTPException(status_code=409, detail="pipeline name or workspace already exists") from exc
    record_audit_event(
        request.state.actor,
        "pipeline.update",
        200,
        detail={"pipeline_id": str(pipeline_id), "fields": sorted(updates)},
    )
    return row


@router.get("/resource-leases")
def list_resource_leases(active_only: bool = True) -> list[dict[str, Any]]:
    where = "where task_id is not null and expires_at >= %s" if active_only else ""
    params = (now_utc(),) if active_only else ()
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"select * from taskhub_resource_leases {where} order by resource_key",
                params,
            )
            return cur.fetchall()


def transition_workflow(
    cur,
    workflow_id: uuid.UUID,
    action: str,
    actor: str,
    reason: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cur.execute("select * from taskhub_workflows where id = %s for update", (workflow_id,))
    workflow = cur.fetchone()
    if not workflow:
        raise HTTPException(status_code=404, detail="workflow not found")
    try:
        resolved = resolve_workflow_transition(workflow["state"], action, workflow.get("resume_state"))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    iteration = workflow["iteration"] + (1 if resolved["increments_iteration"] else 0)
    if iteration > workflow["max_iterations"]:
        raise HTTPException(status_code=409, detail="workflow rework limit reached")
    context = {**(workflow.get("context") or {})}
    if payload:
        outputs = {**(context.get("stage_outputs") or {})}
        outputs[workflow.get("active_role") or workflow["state"]] = redact(payload)
        context["stage_outputs"] = outputs
    timestamp = now_utc()
    cur.execute(
        """
        update taskhub_workflows
        set state = %s, active_role = %s, resume_state = %s, iteration = %s,
            context = %s, updated_at = %s
        where id = %s
        returning *
        """,
        (
            resolved["next_state"],
            resolved.get("current_role"),
            resolved.get("next_resume_state"),
            iteration,
            Jsonb(context),
            timestamp,
            workflow_id,
        ),
    )
    updated = cur.fetchone()
    cur.execute(
        """
        insert into taskhub_workflow_transitions
            (id, workflow_id, from_state, to_state, action, actor, reason, role, iteration, payload, created_at)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            uuid.uuid4(), workflow_id, workflow["state"], resolved["next_state"], action,
            actor, reason, workflow.get("active_role"), iteration, Jsonb(redact(payload or {})), timestamp,
        ),
    )
    return updated


def workflow_id_for_task(task: dict[str, Any]) -> uuid.UUID | None:
    raw = (task.get("metadata") or {}).get("workflow_id")
    try:
        return uuid.UUID(str(raw)) if raw else None
    except ValueError:
        return None


def normalized_implementation_title(title: str) -> str:
    value = str(title or "").strip()
    for prefix in ("真实施工：", "真实施工:", "补齐施工：", "补齐施工:"):
        if value.startswith(prefix):
            return value[len(prefix):].strip()
    return value


def remaining_plan_only_code_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    completed_plan_ids: set[str] = set()
    completed_titles: set[str] = set()
    for task in tasks:
        if task.get("type") != "code.change" or task.get("state") != "succeeded":
            continue
        result = task.get("result") or {}
        if result.get("mode") != "execute":
            continue
        metadata = task.get("metadata") or {}
        plan_task_id = metadata.get("backfill_plan_task_id") or metadata.get("replaces_plan_task_id")
        if plan_task_id:
            completed_plan_ids.add(str(plan_task_id))
        completed_titles.add(normalized_implementation_title(str(task.get("title") or "")))

    remaining = []
    for task in tasks:
        result = task.get("result") or {}
        if task.get("type") != "code.change" or result.get("mode") != "plan_only":
            continue
        task_id = str(task.get("id") or "")
        title = normalized_implementation_title(str(task.get("title") or ""))
        if task_id not in completed_plan_ids and title not in completed_titles:
            remaining.append(task)
    return remaining


def backfill_requirement(workflow: dict[str, Any], plan_task: dict[str, Any]) -> str:
    requirement = str((workflow.get("context") or {}).get("requirement") or workflow.get("summary") or "").strip()
    title = normalized_implementation_title(str(plan_task.get("title") or ""))
    return (
        f"继续完成已经人工批准的施工项：{title}\n\n"
        f"原始业务需求与验收边界：\n{requirement}\n\n"
        "施工要求：读取当前工作副本中已有变更，在其基础上完成本项功能并补充自动化测试；"
        "保持现有架构和安全边界；不得读取或上传凭据、Cookie、Token 或登录态；"
        "不得访问生产系统、发布商品或提交 Git commit。"
    )


@router.post("/workflows/{workflow_id}/backfill-implementation")
def backfill_workflow_implementation(
    workflow_id: uuid.UUID,
    request: Request,
    payload: WorkflowBackfill,
) -> dict[str, Any]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("select * from taskhub_workflows where id = %s for update", (workflow_id,))
            workflow = cur.fetchone()
            if not workflow:
                raise HTTPException(status_code=404, detail="workflow not found")

            cur.execute(
                """
                select * from taskhub_tasks
                where metadata->>'workflow_id' = %s
                order by created_at
                """,
                (str(workflow_id),),
            )
            tasks = cur.fetchall()
            existing = [task for task in tasks if (task.get("metadata") or {}).get("backfill_batch") is True]
            if workflow["state"] == "implementation" and existing:
                return {
                    "workflow_id": str(workflow_id),
                    "state": workflow["state"],
                    "status": "already_running",
                    "groups": len({(task.get("metadata") or {}).get("backfill_plan_task_id") for task in existing}),
                    "task_ids": [str(task["id"]) for task in existing],
                }
            if workflow["state"] != "blocked":
                raise HTTPException(status_code=409, detail=f"workflow is {workflow['state']}; backfill requires blocked state")
            if existing:
                raise HTTPException(status_code=409, detail="backfill tasks already exist; retry the failed task instead")

            remaining = remaining_plan_only_code_tasks(tasks)
            if not remaining:
                raise HTTPException(status_code=409, detail="workflow has no remaining plan-only code tasks")
            if not workflow.get("pipeline_id"):
                raise HTTPException(status_code=409, detail="workflow is not bound to a pipeline")

            rows: list[dict[str, Any]] = []
            previous_test_id: uuid.UUID | None = None
            total = len(remaining)
            for group_index, plan_task in enumerate(remaining, start=1):
                source_metadata = plan_task.get("metadata") or {}
                approval_source_task_id = source_metadata.get("approval_source_task_id")
                approval_content_hash = source_metadata.get("approval_content_hash")
                if not approval_source_task_id or not approval_content_hash:
                    raise HTTPException(status_code=409, detail="plan-only task does not contain approval evidence")
                title = normalized_implementation_title(str(plan_task.get("title") or ""))
                common_metadata = {
                    "workflow_id": str(workflow_id),
                    "stage": "coder_backfill_real_execution",
                    "backfill_batch": True,
                    "backfill_group": group_index,
                    "backfill_group_total": total,
                    "backfill_plan_task_id": str(plan_task["id"]),
                    "approval_source_task_id": str(approval_source_task_id),
                    "approval_content_hash": str(approval_content_hash),
                }
                shared = {
                    "project": workflow["project"],
                    "pipeline_id": workflow["pipeline_id"],
                    "workspace_id": plan_task.get("workspace_id"),
                    "target_worker_id": plan_task.get("target_worker_id"),
                }
                code_row = insert_task(
                    cur,
                    TaskCreate(
                        **shared,
                        type="code.change",
                        title=f"补齐施工：{title}",
                        priority=int(plan_task.get("priority") or 80),
                        input={"mode": "execute", "requirement": backfill_requirement(workflow, plan_task)},
                        metadata=common_metadata,
                        idempotency_key=f"workflow-backfill:{workflow_id}:{plan_task['id']}:code",
                    ),
                    request.state.actor,
                    "real implementation backfill requested from workflow workbench",
                )
                if previous_test_id:
                    add_task_dependency(cur, code_row["id"], previous_test_id)
                diff_row = insert_task(
                    cur,
                    TaskCreate(
                        **shared,
                        type="code.diff.preview",
                        title=f"补齐差异：{title}",
                        priority=int(plan_task.get("priority") or 80) - 1,
                        input={"paths": []},
                        metadata=common_metadata,
                        idempotency_key=f"workflow-backfill:{workflow_id}:{plan_task['id']}:diff",
                    ),
                    request.state.actor,
                    "diff verification created for implementation backfill",
                )
                add_task_dependency(cur, diff_row["id"], code_row["id"])
                test_row = insert_task(
                    cur,
                    TaskCreate(
                        **shared,
                        type="test.run",
                        title=f"同工作副本测试：{title}",
                        priority=int(plan_task.get("priority") or 80) - 2,
                        input={"command": "check"},
                        metadata=common_metadata,
                        idempotency_key=f"workflow-backfill:{workflow_id}:{plan_task['id']}:test",
                    ),
                    request.state.actor,
                    "same-workspace test created for implementation backfill",
                )
                add_task_dependency(cur, test_row["id"], diff_row["id"])
                previous_test_id = test_row["id"]
                rows.extend((code_row, diff_row, test_row))

            timestamp = now_utc()
            cur.execute(
                """
                update taskhub_workflow_role_runs
                set status = 'superseded', completed_at = %s,
                    error = %s, next_retry_at = null
                where workflow_id = %s and status = 'running'
                """,
                (
                    timestamp,
                    Jsonb({"reason": "implementation backfill superseded the premature role run"}),
                    workflow_id,
                ),
            )
            updated = transition_workflow(
                cur,
                workflow_id,
                "backfill_implementation",
                request.state.actor,
                payload.reason,
                {"backfill_groups": total, "backfill_task_ids": [str(row["id"]) for row in rows]},
            )
        conn.commit()
    return {
        "workflow_id": str(workflow_id),
        "state": updated["state"],
        "status": "created",
        "groups": total,
        "tasks_created": len(rows),
        "task_ids": [str(row["id"]) for row in rows],
    }


@router.post("/workflows/{workflow_id}/auto-recover")
def auto_recover_workflow(workflow_id: uuid.UUID, request: Request, payload: TaskAction) -> dict[str, Any]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("select * from taskhub_workflows where id = %s for update", (workflow_id,))
            workflow = cur.fetchone()
            if not workflow:
                raise HTTPException(status_code=404, detail="workflow not found")
            if workflow["state"] != "blocked" or workflow.get("resume_state") != "implementation":
                raise HTTPException(status_code=409, detail="workflow is not blocked during implementation")
            cur.execute(
                """
                select * from taskhub_tasks
                where metadata->>'workflow_id' = %s and state = 'failed'
                  and type = any(%s)
                order by updated_at desc
                limit 1
                for update
                """,
                (str(workflow_id), list(AUTO_REWORK_FAILURE_TYPES)),
            )
            failed_task = cur.fetchone()
            if not failed_task:
                raise HTTPException(status_code=409, detail="workflow has no automatically recoverable failed gate")
            recovery = schedule_automatic_rework(
                cur, failed_task, failed_task.get("error") or {}, request.state.actor,
            )
            if not recovery:
                raise HTTPException(status_code=409, detail="automatic rework limit or routing prerequisites were not met")
            transition_workflow(
                cur,
                workflow_id,
                "retry",
                "taskhub:auto-rework",
                payload.reason,
                {
                    "failed_task_id": str(failed_task["id"]),
                    "repair_task_id": str(recovery["repair_task"]["id"]),
                    "repair_kind": recovery["repair_kind"],
                    "automatic": True,
                },
            )
        conn.commit()
    return {
        "workflow_id": str(workflow_id),
        "state": "implementation",
        "status": "automatic_rework_queued",
        "failed_task_id": str(failed_task["id"]),
        "repair_task_id": str(recovery["repair_task"]["id"]),
        "repair_kind": recovery["repair_kind"],
    }


def reconcile_workflow_tasks(cur, task: dict[str, Any], actor: str) -> None:
    workflow_id = workflow_id_for_task(task)
    if not workflow_id or not (task.get("metadata") or {}).get("approval_source_task_id"):
        return
    cur.execute("select * from taskhub_workflows where id = %s", (workflow_id,))
    workflow = cur.fetchone()
    if not workflow or workflow["state"] != "implementation":
        return
    cur.execute(
        """
        select state, count(*) as count
        from taskhub_tasks
        where metadata->>'workflow_id' = %s and metadata ? 'approval_source_task_id'
        group by state
        """,
        (str(workflow_id),),
    )
    counts = {row["state"]: row["count"] for row in cur.fetchall()}
    if counts.get("failed") or counts.get("blocked") or counts.get("canceled"):
        transition_workflow(
            cur, workflow_id, "block", actor, "implementation task requires intervention", {"task_states": counts}
        )
    elif counts and sum(counts.values()) == counts.get("succeeded", 0):
        transition_workflow(
            cur, workflow_id, "implementation_done", actor, "all approved implementation tasks succeeded",
            {"task_states": counts},
        )


def automatic_rework_requirement(workflow: dict[str, Any], failed_task: dict[str, Any], error: dict[str, Any]) -> str:
    original_requirement = str((workflow.get("context") or {}).get("requirement") or workflow.get("summary") or "")
    failure = json.dumps(redact(error), ensure_ascii=False, default=str)[:12000]
    return (
        "TaskHub detected a failed implementation quality gate. Diagnose and fix the root cause in the current "
        "pipeline worktree, then leave the worktree ready for the same gate to run again. Keep the change minimal; "
        "do not weaken, skip, delete, or mark tests as expected failures merely to make the gate pass. Preserve the "
        "approved scope and all safety boundaries. Do not commit, deploy, access production systems or production "
        "data, or read/upload credentials.\n\n"
        f"Failed gate: {failed_task.get('title') or failed_task.get('type')}\n"
        f"Gate input: {json.dumps(redact(failed_task.get('input') or {}), ensure_ascii=False, default=str)}\n"
        f"Failure evidence: {failure}\n\n"
        f"Original approved requirement:\n{original_requirement[:12000]}"
    )


def failure_needs_workspace_bootstrap(error: dict[str, Any]) -> bool:
    evidence = json.dumps(error, ensure_ascii=False, default=str).lower()
    return error.get("exit_code") == 127 or "command not found" in evidence or "not found\n" in evidence


def automatic_rework_category(error: dict[str, Any]) -> str:
    evidence = json.dumps(error, ensure_ascii=False, default=str).lower()
    if failure_needs_workspace_bootstrap(error):
        return "workspace_environment"
    if "ruff check" in evidence or " f401" in evidence or " i001" in evidence:
        return "lint"
    if "mypy" in evidence or "typecheck" in evidence:
        return "typecheck"
    if "pytest" in evidence or "failed " in evidence:
        return "tests"
    if "vite" in evidence or "tsc" in evidence or "eslint" in evidence:
        return "frontend_check"
    return "quality_gate"


def schedule_automatic_rework(
    cur,
    task: dict[str, Any],
    error: dict[str, Any],
    actor: str,
) -> dict[str, Any] | None:
    if task.get("type") not in AUTO_REWORK_FAILURE_TYPES or not task.get("pipeline_id"):
        return None
    workflow_id = workflow_id_for_task(task)
    metadata = task.get("metadata") or {}
    if not workflow_id or not metadata.get("approval_source_task_id"):
        return None
    cur.execute("select * from taskhub_workflows where id = %s for update", (workflow_id,))
    workflow = cur.fetchone()
    if not workflow or workflow["state"] not in {"implementation", "blocked"}:
        return None
    category = automatic_rework_category(error)
    environment_repair = category == "workspace_environment"
    same_policy = int(metadata.get("automatic_rework_policy_version") or 0) == AUTO_REWORK_POLICY_VERSION
    previous_category = str(metadata.get("automatic_rework_category") or "") if same_policy else ""
    attempt = int(metadata.get("automatic_category_attempt") or 0) + 1 if previous_category == category else 1
    category_limit = 1 if environment_repair else int(workflow.get("max_iterations") or 3)
    policy_attempt = int(metadata.get("automatic_policy_attempt") or 0) + 1 if same_policy else 1
    retry_count = int(task.get("retry_count") or 0) + 1
    if attempt > category_limit or policy_attempt > int(workflow.get("max_iterations") or 3) * 3:
        return None
    cur.execute("select * from taskhub_pipelines where id = %s", (task["pipeline_id"],))
    pipeline = cur.fetchone()
    if not pipeline or pipeline["state"] != "active" or not pipeline.get("default_worker_id"):
        return None

    worker_id = pipeline["default_worker_id"]
    workspace_id = pipeline["workspace_id"]
    common_metadata = {
        **metadata,
        "stage": "automatic_test_rework",
        "automatic_rework": True,
        "automatic_rework_policy_version": AUTO_REWORK_POLICY_VERSION,
        "automatic_policy_attempt": policy_attempt,
        "automatic_rework_attempt": attempt,
        "automatic_rework_for_task_id": str(task["id"]),
        "automatic_rework_category": category,
        "automatic_category_attempt": attempt,
        "automatic_total_attempt": retry_count,
    }
    code_task = None
    diff_task = None
    if environment_repair:
        repair_task = insert_task(
            cur,
            TaskCreate(
                project=task["project"],
                type="workspace.bootstrap",
                title=f"自动恢复节点依赖: {task['title']}",
                priority=min(100, int(task.get("priority") or 50) + 10),
                input={},
                metadata=common_metadata,
                idempotency_key=f"auto-rework:v{AUTO_REWORK_POLICY_VERSION}:{task['id']}:environment:{attempt}",
                pipeline_id=task["pipeline_id"],
                workspace_id=workspace_id,
                target_worker_id=worker_id,
            ),
            "taskhub:auto-rework",
            "missing workspace dependency created an automatic bootstrap task",
        )
    else:
        code_task = insert_task(
            cur,
            TaskCreate(
                project=task["project"],
                type="code.change",
                title=f"自动返工 {attempt}: {task['title']}",
                priority=min(100, int(task.get("priority") or 50) + 10),
                input={"mode": "execute", "requirement": automatic_rework_requirement(workflow, task, error)},
                metadata=common_metadata,
                idempotency_key=f"auto-rework:v{AUTO_REWORK_POLICY_VERSION}:{task['id']}:{category}:{attempt}:code",
                pipeline_id=task["pipeline_id"],
                workspace_id=workspace_id,
                target_worker_id=worker_id,
            ),
            "taskhub:auto-rework",
            "quality gate failure created an automatic corrective implementation",
        )
        diff_task = insert_task(
            cur,
            TaskCreate(
                project=task["project"],
                type="code.diff.preview",
                title=f"自动返工差异检查 {attempt}: {task['title']}",
                priority=min(99, int(task.get("priority") or 50) + 9),
                input={"paths": []},
                metadata=common_metadata,
                idempotency_key=f"auto-rework:v{AUTO_REWORK_POLICY_VERSION}:{task['id']}:{category}:{attempt}:diff",
                depends_on=[code_task["id"]],
                pipeline_id=task["pipeline_id"],
                workspace_id=workspace_id,
                target_worker_id=worker_id,
            ),
            "taskhub:auto-rework",
            "automatic corrective diff verification created",
        )
        repair_task = diff_task
    retry_input = task.get("input") or {}
    if task["type"] == "test.run" and not retry_input.get("command"):
        retry_input = {**retry_input, "command": "check"}
    validate_task_input(task["type"], retry_input)
    add_task_dependency(cur, task["id"], repair_task["id"])
    cur.execute(
        """
        update taskhub_tasks
        set state = 'pending', input = %s, metadata = %s, error = null,
            worker_id = null, target_worker_id = %s, workspace_id = %s,
            lease_token = null, lease_expires_at = null, heartbeat_at = null,
            claimed_at = null, completed_at = null, retry_count = %s, updated_at = %s
        where id = %s
        returning *
        """,
        (
            Jsonb(redact(retry_input)), Jsonb(redact(common_metadata)), worker_id, workspace_id,
            retry_count, now_utc(), task["id"],
        ),
    )
    retried_task = cur.fetchone()
    add_history(
        cur,
        task["id"],
        "failed",
        "pending",
        "taskhub:auto-rework",
        "quality gate queued behind automatic corrective implementation",
        {
            "attempt": attempt,
            "repair_kind": "workspace_bootstrap" if environment_repair else "code_change",
            "repair_task_id": str(repair_task["id"]),
        },
    )
    requeued_descendants = requeue_dependency_blocked_descendants(cur, task["id"], "taskhub:auto-rework")
    return {
        "workflow": workflow,
        "task": retried_task,
        "code_task": code_task,
        "diff_task": diff_task,
        "repair_task": repair_task,
        "repair_kind": "workspace_bootstrap" if environment_repair else "code_change",
        "requeued_descendants": requeued_descendants,
    }


@router.get("/workflows")
def list_workflows(
    project: str | None = None,
    state: str | None = None,
    pipeline_id: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> list[dict[str, Any]]:
    filters: list[str] = []
    params: list[Any] = []
    if project:
        filters.append("workflow.project = %s")
        params.append(project)
    if state:
        if state not in WORKFLOW_STATES:
            raise HTTPException(status_code=400, detail="invalid workflow state")
        filters.append("workflow.state = %s")
        params.append(state)
    if pipeline_id:
        filters.append("workflow.pipeline_id = %s")
        params.append(pipeline_id)
    where = f"where {' and '.join(filters)}" if filters else ""
    params.append(limit)
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                select workflow.*,
                       role_run.status as role_run_status,
                       role_run.verdict as role_run_verdict,
                       role_run.summary as role_run_summary,
                       role_run.provider as role_run_provider,
                       role_run.model as role_run_model,
                       role_run.completed_at as role_run_completed_at
                from taskhub_workflows workflow
                left join lateral (
                    select transition.id
                    from taskhub_workflow_transitions transition
                    where transition.workflow_id = workflow.id
                      and transition.to_state = workflow.state
                    order by transition.created_at desc
                    limit 1
                ) current_transition on true
                left join lateral (
                    select run.*
                    from taskhub_workflow_role_runs run
                    where run.workflow_id = workflow.id
                      and run.entry_transition_id = current_transition.id
                    order by run.started_at desc
                    limit 1
                ) role_run on true
                {where}
                order by workflow.created_at desc
                limit %s
                """,
                params,
            )
            return cur.fetchall()


@router.get("/workflows/{workflow_id}")
def get_workflow(workflow_id: uuid.UUID) -> dict[str, Any]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("select * from taskhub_workflows where id = %s", (workflow_id,))
            workflow = cur.fetchone()
            if not workflow:
                raise HTTPException(status_code=404, detail="workflow not found")
            cur.execute(
                "select * from taskhub_role_evidence where workflow_id = %s order by ordinal",
                (workflow_id,),
            )
            evidence = cur.fetchall()
            cur.execute(
                "select * from taskhub_workflow_transitions where workflow_id = %s order by created_at",
                (workflow_id,),
            )
            transitions = cur.fetchall()
            cur.execute(
                "select * from taskhub_workflow_role_runs where workflow_id = %s order by started_at",
                (workflow_id,),
            )
            role_runs = cur.fetchall()
            cur.execute(
                "select id, type, title, state, worker_id, target_worker_id, updated_at from taskhub_tasks where metadata->>'workflow_id' = %s order by created_at",
                (str(workflow_id),),
            )
            tasks = cur.fetchall()
            cur.execute(
                "select * from taskhub_task_handoffs where workflow_id = %s order by created_at",
                (workflow_id,),
            )
            handoffs = cur.fetchall()
    return {
        **workflow,
        "context": redact(workflow.get("context")),
        "evidence": [{**row, "payload": redact(row.get("payload"))} for row in evidence],
        "transitions": [{**row, "payload": redact(row.get("payload"))} for row in transitions],
        "role_runs": [
            {**row, "output": redact(row.get("output")), "error": redact(row.get("error"))}
            for row in role_runs
        ],
        "tasks": tasks,
        "handoffs": handoffs,
    }


@router.post("/workflows/{workflow_id}/actions")
def act_on_workflow(workflow_id: uuid.UUID, request: Request, payload: WorkflowAction) -> dict[str, Any]:
    with connect() as conn:
        with conn.cursor() as cur:
            workflow = transition_workflow(
                cur, workflow_id, payload.action, request.state.actor, payload.reason, payload.output
            )
        conn.commit()
    return {**workflow, "context": redact(workflow.get("context"))}


def add_history(
    cur,
    task_id: uuid.UUID,
    from_state: str | None,
    to_state: str,
    actor: str,
    reason: str,
    payload: dict[str, Any] | None = None,
) -> None:
    cur.execute(
        """
        insert into taskhub_task_history (id, task_id, from_state, to_state, actor, reason, payload, created_at)
        values (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            uuid.uuid4(),
            task_id,
            from_state,
            to_state,
            actor,
            reason,
            Jsonb(redact(payload or {})),
            now_utc(),
        ),
    )


def load_task_for_update(cur, task_id: uuid.UUID) -> dict[str, Any]:
    cur.execute("select * from taskhub_tasks where id = %s for update", (task_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="task not found")
    return row


def assert_transition(from_state: str, to_state: str) -> None:
    if from_state not in TASK_STATES or to_state not in TASK_STATES:
        raise HTTPException(status_code=400, detail="invalid task state")
    if (from_state, to_state) not in ALLOWED_TRANSITIONS:
        raise HTTPException(status_code=409, detail=f"invalid transition: {from_state} -> {to_state}")


def assert_human_approval(task: dict[str, Any]) -> None:
    if task["type"] != "review.human":
        raise HTTPException(status_code=409, detail="only review.human tasks can be approved")
    if task["state"] not in {"pending", "blocked"}:
        raise HTTPException(status_code=409, detail=f"cannot approve task in state: {task['state']}")


@router.post("/tasks")
def create_task(request: Request, payload: TaskCreate) -> dict[str, Any]:
    return create_task_record(payload, actor=request.state.actor, reason="task created from API")


def insert_task(cur, request: TaskCreate, actor: str, reason: str) -> dict[str, Any]:
    task_id = uuid.uuid4()
    created_at = now_utc()
    if request.type in UNSCHEDULABLE_TASK_TYPES:
        raise HTTPException(status_code=400, detail="task type is outside TaskHub scope")
    try:
        validate_task_input(request.type, request.input)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    approval_hash = canonical_hash(request.input.get("approval_plan", [])) if request.type == "review.human" else None
    contract = handoff_contract(request.type, request.input, request.metadata)
    workspace_id = request.workspace_id.strip() if request.workspace_id else None
    target_worker_id = request.target_worker_id.strip() if request.target_worker_id else None
    pipeline_worker_id = None
    if not target_worker_id:
        legacy_target = request.metadata.get("required_worker")
        target_worker_id = str(legacy_target).strip() if legacy_target else None
    if request.pipeline_id:
        cur.execute("select * from taskhub_pipelines where id = %s", (request.pipeline_id,))
        pipeline = cur.fetchone()
        if not pipeline:
            raise HTTPException(status_code=400, detail="pipeline does not exist")
        if pipeline["project"] != request.project:
            raise HTTPException(status_code=400, detail="pipeline belongs to a different project")
        if pipeline["state"] in {"completed", "canceled"}:
            raise HTTPException(status_code=409, detail=f"pipeline is {pipeline['state']}")
        workspace_id = workspace_id or pipeline["workspace_id"]
        pipeline_worker_id = pipeline["default_worker_id"]
    cur.execute("select id,state from taskhub_projects where slug=%s", (request.project,))
    registered_project = cur.fetchone()
    if registered_project:
        if registered_project["state"] != "active":
            raise HTTPException(status_code=409, detail=f"project is {registered_project['state']}")
        if workspace_id:
            cur.execute(
                """select id,pipeline_id from taskhub_project_workspaces
                where project_id=%s and workspace_id=%s""",
                (registered_project["id"], workspace_id),
            )
            registered_workspace = cur.fetchone()
            if not registered_workspace:
                raise HTTPException(status_code=409, detail="workspace is not registered for this project")
            if request.pipeline_id and registered_workspace["pipeline_id"] != request.pipeline_id:
                raise HTTPException(status_code=409, detail="workspace is bound to a different pipeline")
    if not target_worker_id:
        target_worker_id = automatic_target_worker(cur, request.type, pipeline_worker_id)
    resource_keys = normalize_resource_keys(request.resource_keys, workspace_id)
    cur.execute(
        """
        insert into taskhub_tasks
            (id, project, type, title, state, priority, input, metadata, idempotency_key,
             approval_content_hash, pipeline_id, workspace_id, target_worker_id, resource_keys,
             retry_count, created_at, updated_at)
        values
            (%s, %s, %s, %s, 'pending', %s, %s, %s, %s, %s, %s, %s, %s, %s, 0, %s, %s)
        on conflict (project, idempotency_key) where idempotency_key is not null do nothing
        returning *
        """,
        (
            task_id,
            request.project,
            request.type,
            request.title,
            request.priority,
            Jsonb(redact(request.input)),
            Jsonb(redact(request.metadata)),
            request.idempotency_key,
            approval_hash,
            request.pipeline_id,
            workspace_id,
            target_worker_id,
            resource_keys,
            created_at,
            created_at,
        ),
    )
    row = cur.fetchone()
    if not row:
        cur.execute(
            "select * from taskhub_tasks where project = %s and idempotency_key = %s",
            (request.project, request.idempotency_key),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=409, detail="idempotency conflict")
        return row
    add_history(cur, task_id, None, "pending", actor, reason, {"title": request.title})
    workflow_id = request.metadata.get("workflow_id")
    try:
        workflow_uuid = uuid.UUID(str(workflow_id)) if workflow_id else None
    except ValueError:
        workflow_uuid = None
    cur.execute(
        """
        insert into taskhub_task_handoffs
            (id, task_id, workflow_id, contract_version, producer, consumer, status,
             contract, payload, created_at, updated_at)
        values (%s, %s, %s, %s, %s, %s, 'pending', %s, '{}'::jsonb, %s, %s)
        on conflict (task_id) do nothing
        """,
        (
            uuid.uuid4(), task_id, workflow_uuid, contract["version"], contract["producer"],
            contract["consumer"], Jsonb(contract), created_at, created_at,
        ),
    )
    for dependency_id in request.depends_on:
        add_task_dependency(cur, task_id, dependency_id)
    return row


def add_task_dependency(cur, task_id: uuid.UUID, depends_on_task_id: uuid.UUID) -> None:
    if task_id == depends_on_task_id:
        raise HTTPException(status_code=400, detail="task cannot depend on itself")
    cur.execute(
        "select id, project from taskhub_tasks where id = any(%s)",
        ([task_id, depends_on_task_id],),
    )
    rows = cur.fetchall()
    if len(rows) != 2:
        raise HTTPException(status_code=400, detail="dependency task does not exist")
    if len({row["project"] for row in rows}) != 1:
        raise HTTPException(status_code=400, detail="dependency must be in the same project")
    cur.execute(
        """
        with recursive ancestors(id) as (
            select depends_on_task_id from taskhub_task_dependencies where task_id = %s
            union
            select dependency.depends_on_task_id
            from taskhub_task_dependencies dependency
            join ancestors on dependency.task_id = ancestors.id
        )
        select 1 from ancestors where id = %s limit 1
        """,
        (depends_on_task_id, task_id),
    )
    if cur.fetchone():
        raise HTTPException(status_code=409, detail="dependency would create a cycle")
    cur.execute(
        """
        insert into taskhub_task_dependencies (task_id, depends_on_task_id, created_at)
        values (%s, %s, %s)
        on conflict do nothing
        """,
        (task_id, depends_on_task_id, now_utc()),
    )


def create_task_record(request: TaskCreate, actor: str = "taskhub", reason: str = "task created") -> dict[str, Any]:
    with connect() as conn:
        with conn.cursor() as cur:
            row = insert_task(cur, request, actor, reason)
        conn.commit()
    return serialize_task(row)


def create_task_graph(
    requests: list[TaskCreate],
    dependency_indices: dict[int, list[int]],
    actor: str = "taskhub",
    reason: str = "task graph created",
) -> list[dict[str, Any]]:
    with connect() as conn:
        with conn.cursor() as cur:
            rows = [insert_task(cur, request, actor, reason) for request in requests]
            for task_index, parent_indices in dependency_indices.items():
                if task_index < 0 or task_index >= len(rows):
                    raise HTTPException(status_code=400, detail="dependency task index is invalid")
                for parent_index in parent_indices:
                    if parent_index < 0 or parent_index >= task_index:
                        raise HTTPException(status_code=400, detail="dependencies must reference an earlier task")
                    add_task_dependency(cur, rows[task_index]["id"], rows[parent_index]["id"])
        conn.commit()
    return [serialize_task(row) for row in rows]


def downstream_tasks_for_approval(task: dict[str, Any], approval_plan_override: list[dict[str, Any]] | None = None) -> list[TaskCreate]:
    task_input = task.get("input") or {}
    approval_plan = approval_plan_override if approval_plan_override is not None else task_input.get("approval_plan") or []
    if not isinstance(approval_plan, list):
        raise HTTPException(status_code=400, detail="approval_plan must be a list")

    downstream: list[TaskCreate] = []
    approved_plan_hash = canonical_hash(approval_plan)
    for index, item in enumerate(approval_plan):
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail=f"approval_plan[{index}] must be an object")
        task_type = str(item.get("type") or "").strip()
        if task_type not in APPROVAL_DOWNSTREAM_TYPES:
            raise HTTPException(status_code=400, detail=f"approval downstream type is not allowed: {task_type}")
        title = str(item.get("title") or f"审批后任务: {task_type}").strip()
        priority = int(item.get("priority", task["priority"]))
        task_metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        task_payload = item.get("input") if isinstance(item.get("input"), dict) else {}
        downstream.append(
            TaskCreate(
                project=task["project"],
                type=task_type,
                title=title,
                priority=priority,
                input=task_payload,
                metadata={
                    **(task.get("metadata") or {}),
                    **task_metadata,
                    "approval_source_task_id": str(task["id"]),
                    "approval_content_hash": approved_plan_hash,
                },
                idempotency_key=f"approval:{task['id']}:{approved_plan_hash}:{index}",
                pipeline_id=task.get("pipeline_id"),
                workspace_id=str(item.get("workspace_id") or task.get("workspace_id") or "") or None,
                target_worker_id=str(item.get("target_worker_id") or "") or None,
                resource_keys=item.get("resource_keys") if isinstance(item.get("resource_keys"), list) else [],
            )
        )
    return downstream


@router.get("/tasks")
def list_tasks(
    project: str | None = None,
    state: str | None = None,
    type: str | None = None,
    worker_id: str | None = None,
    pipeline_id: uuid.UUID | None = None,
    target_worker_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    filters = []
    params: list[Any] = []
    if project:
        filters.append("project = %s")
        params.append(project)
    if state:
        if state not in TASK_STATES:
            raise HTTPException(status_code=400, detail="invalid state")
        filters.append("state = %s")
        params.append(state)
    if type:
        filters.append("type = %s")
        params.append(type)
    if worker_id:
        filters.append("worker_id = %s")
        params.append(worker_id)
    if pipeline_id:
        filters.append("pipeline_id = %s")
        params.append(pipeline_id)
    if target_worker_id:
        filters.append("target_worker_id = %s")
        params.append(target_worker_id)

    where = f"where {' and '.join(filters)}" if filters else ""
    params.extend([limit, offset])
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                select * from taskhub_tasks
                {where}
                order by
                    case state
                        when 'blocked' then 1
                        when 'failed' then 2
                        when 'running' then 3
                        when 'pending' then 4
                        when 'succeeded' then 5
                        when 'canceled' then 6
                        else 7
                    end,
                    updated_at desc
                limit %s offset %s
                """,
                params,
            )
            rows = cur.fetchall()
    return [serialize_task(row) for row in rows]


@router.get("/stats")
def task_stats(project: str | None = None, pipeline_id: uuid.UUID | None = None) -> dict[str, Any]:
    params: list[Any] = []
    filters: list[str] = []
    if project:
        filters.append("project = %s")
        params.append(project)
    if pipeline_id:
        filters.append("pipeline_id = %s")
        params.append(pipeline_id)
    where = f"where {' and '.join(filters)}" if filters else ""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                select state, count(*) as count
                from taskhub_tasks
                {where}
                group by state
                """,
                params,
            )
            rows = cur.fetchall()
    counts = {state: 0 for state in sorted(TASK_STATES)}
    counts.update({row["state"]: row["count"] for row in rows})
    return {"project": project, "pipeline_id": pipeline_id, "states": counts, "total": sum(counts.values())}


@router.get("/audit")
def list_audit_events(limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select * from taskhub_audit_events order by created_at desc limit %s",
                (limit,),
            )
            rows = cur.fetchall()
    return [{**row, "detail": redact(row.get("detail"))} for row in rows]


@router.get("/tasks/{task_id}")
def get_task(task_id: uuid.UUID) -> dict[str, Any]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("select * from taskhub_tasks where id = %s", (task_id,))
            task = cur.fetchone()
            if not task:
                raise HTTPException(status_code=404, detail="task not found")
            cur.execute(
                "select * from taskhub_task_history where task_id = %s order by created_at desc",
                (task_id,),
            )
            history = cur.fetchall()
            cur.execute(
                """
                select parent.id, parent.type, parent.title, parent.state
                from taskhub_task_dependencies dependency
                join taskhub_tasks parent on parent.id = dependency.depends_on_task_id
                where dependency.task_id = %s
                order by parent.created_at
                """,
                (task_id,),
            )
            dependencies = cur.fetchall()
            cur.execute(
                """
                select child.id, child.type, child.title, child.state
                from taskhub_task_dependencies dependency
                join taskhub_tasks child on child.id = dependency.task_id
                where dependency.depends_on_task_id = %s
                order by child.created_at
                """,
                (task_id,),
            )
            dependents = cur.fetchall()
    payload = serialize_task(task)
    payload["history"] = [serialize_history(row) for row in history]
    payload["dependencies"] = dependencies
    payload["dependents"] = dependents
    return payload


def block_tasks_with_failed_dependencies(cur, project: str, actor: str) -> None:
    cur.execute(
        """
        select child.*
        from taskhub_tasks child
        where child.project = %s and child.state = 'pending'
          and exists (
              select 1
              from taskhub_task_dependencies dependency
              join taskhub_tasks parent on parent.id = dependency.depends_on_task_id
              where dependency.task_id = child.id
                and parent.state in ('failed', 'blocked', 'canceled')
          )
        for update of child skip locked
        """,
        (project,),
    )
    for task in cur.fetchall():
        error = {"code": "DEPENDENCY_FAILED", "message": "upstream dependency did not succeed"}
        cur.execute(
            "update taskhub_tasks set state = 'blocked', error = %s, updated_at = %s where id = %s",
            (Jsonb(error), now_utc(), task["id"]),
        )
        add_history(cur, task["id"], "pending", "blocked", actor, "upstream dependency failed", error)


def requeue_dependency_blocked_descendants(cur, task_id: uuid.UUID, actor: str) -> int:
    cur.execute(
        """
        with recursive descendants(id) as (
            select dependency.task_id
            from taskhub_task_dependencies dependency
            where dependency.depends_on_task_id = %s
            union
            select dependency.task_id
            from taskhub_task_dependencies dependency
            join descendants parent on parent.id = dependency.depends_on_task_id
        )
        select task.* from taskhub_tasks task
        where task.id in (select id from descendants)
          and task.state = 'blocked'
          and task.error->>'code' = 'DEPENDENCY_FAILED'
        for update
        """,
        (task_id,),
    )
    descendants = cur.fetchall()
    timestamp = now_utc()
    for descendant in descendants:
        cur.execute(
            "update taskhub_tasks set state='pending', error=null, updated_at=%s where id=%s",
            (timestamp, descendant["id"]),
        )
        add_history(
            cur, descendant["id"], "blocked", "pending", actor,
            "upstream task was prepared for retry", {"upstream_task_id": str(task_id)},
        )
    return len(descendants)


def release_resource_leases(cur, task_id: uuid.UUID) -> None:
    cur.execute(
        """
        update taskhub_resource_leases
        set task_id = null, pipeline_id = null, worker_id = null, lease_token = null,
            expires_at = null, updated_at = %s
        where task_id = %s
        """,
        (now_utc(), task_id),
    )


def acquire_resource_leases(
    cur,
    task: dict[str, Any],
    worker_id: str,
    lease_token: uuid.UUID,
    expires_at: datetime,
) -> bool:
    resource_keys = normalize_resource_keys(task.get("resource_keys") or [], task.get("workspace_id"))
    if not resource_keys:
        return True
    timestamp = now_utc()
    for resource_key in resource_keys:
        cur.execute(
            """
            insert into taskhub_resource_leases (resource_key, created_at, updated_at)
            values (%s, %s, %s)
            on conflict (resource_key) do nothing
            """,
            (resource_key, timestamp, timestamp),
        )
    cur.execute(
        """
        select * from taskhub_resource_leases
        where resource_key = any(%s)
        order by resource_key
        for update
        """,
        (resource_keys,),
    )
    leases = cur.fetchall()
    for lease in leases:
        if lease.get("task_id") and lease["task_id"] != task["id"]:
            if lease.get("expires_at") and lease["expires_at"] >= timestamp:
                return False
    cur.execute(
        """
        update taskhub_resource_leases
        set task_id = %s, pipeline_id = %s, worker_id = %s, lease_token = %s,
            expires_at = %s, updated_at = %s
        where resource_key = any(%s)
        """,
        (
            task["id"],
            task.get("pipeline_id"),
            worker_id,
            lease_token,
            expires_at,
            timestamp,
            resource_keys,
        ),
    )
    return True


@router.post("/claim")
def claim_task(request: TaskClaim) -> dict[str, Any]:
    type_filter = "and task.type <> all(%s)"
    params: list[Any] = [request.project, list(UNSCHEDULABLE_TASK_TYPES)]
    requested_types = [item for item in request.types if item not in UNSCHEDULABLE_TASK_TYPES]
    if request.types:
        if not requested_types:
            return {"task": None}
        type_filter += " and task.type = any(%s)"
        params.append(requested_types)

    claimed_at = now_utc()
    lease_token = uuid.uuid4()
    lease_expires_at = claimed_at + lease_duration()
    with connect() as conn:
        with conn.cursor() as cur:
            block_tasks_with_failed_dependencies(cur, request.project, "taskhub:dependency-reconciler")
            cur.execute(
                """
                update taskhub_resource_leases
                set task_id = null, pipeline_id = null, worker_id = null, lease_token = null,
                    expires_at = null, updated_at = %s
                where task_id is not null and expires_at < %s
                """,
                (claimed_at, claimed_at),
            )
            cur.execute(
                """
                select * from taskhub_tasks
                where state = 'running' and lease_expires_at < %s
                for update skip locked
                """,
                (claimed_at,),
            )
            for expired in cur.fetchall():
                release_resource_leases(cur, expired["id"])
                cur.execute(
                    """
                    update taskhub_tasks
                    set state = 'pending', worker_id = null, lease_token = null,
                        lease_expires_at = null, heartbeat_at = null, claimed_at = null,
                        retry_count = retry_count + 1, updated_at = %s
                    where id = %s
                    """,
                    (claimed_at, expired["id"]),
                )
                add_history(
                    cur,
                    expired["id"],
                    "running",
                    "pending",
                    "taskhub:lease-reaper",
                    "worker lease expired",
                    {"previous_worker_id": expired.get("worker_id")},
                )
            cur.execute(
                f"""
                select task.* from taskhub_tasks task
                where task.state = 'pending' and task.project = %s
                {type_filter}
                and (task.target_worker_id is null or task.target_worker_id = %s)
                and (
                    task.pipeline_id is null
                    or exists (
                        select 1 from taskhub_pipelines pipeline
                        where pipeline.id = task.pipeline_id and pipeline.state = 'active'
                    )
                )
                and not exists (
                    select 1
                    from taskhub_task_dependencies dependency
                    join taskhub_tasks parent on parent.id = dependency.depends_on_task_id
                    where dependency.task_id = task.id and parent.state <> 'succeeded'
                )
                order by priority desc, created_at
                for update skip locked
                limit 20
                """,
                [*params, request.worker_id],
            )
            task = None
            for candidate in cur.fetchall():
                if acquire_resource_leases(cur, candidate, request.worker_id, lease_token, lease_expires_at):
                    task = candidate
                    break
            if not task:
                conn.commit()
                return {"task": None}

            assert_transition(task["state"], "running")
            cur.execute(
                """
                update taskhub_tasks
                set state = 'running', worker_id = %s, claimed_at = %s, heartbeat_at = %s,
                    lease_token = %s, lease_expires_at = %s, updated_at = %s
                where id = %s
                returning *
                """,
                (
                    request.worker_id,
                    claimed_at,
                    claimed_at,
                    lease_token,
                    lease_expires_at,
                    claimed_at,
                    task["id"],
                ),
            )
            row = cur.fetchone()
            add_history(cur, task["id"], task["state"], "running", request.worker_id, "task claimed")
        conn.commit()
    return {"task": serialize_task(row, include_lease_token=True)}


def assert_lease_owner(task: dict[str, Any], worker_id: str, lease_token: str) -> None:
    if task["worker_id"] != worker_id:
        raise HTTPException(status_code=409, detail="worker does not own task")
    if not task.get("lease_token") or not hmac_compare_uuid(task["lease_token"], lease_token):
        raise HTTPException(status_code=409, detail="lease token does not match")


def hmac_compare_uuid(expected: Any, supplied: str) -> bool:
    try:
        return uuid.UUID(str(expected)) == uuid.UUID(supplied)
    except ValueError:
        return False


@router.post("/tasks/{task_id}/heartbeat")
def heartbeat_task(task_id: uuid.UUID, request: TaskHeartbeat) -> dict[str, Any]:
    heartbeat_at = now_utc()
    with connect() as conn:
        with conn.cursor() as cur:
            task = load_task_for_update(cur, task_id)
            if task["state"] != "running":
                raise HTTPException(status_code=409, detail="task is not running")
            assert_lease_owner(task, request.worker_id, request.lease_token)
            cur.execute(
                """
                update taskhub_tasks
                set heartbeat_at = %s, lease_expires_at = %s, updated_at = %s
                where id = %s
                returning lease_expires_at
                """,
                (heartbeat_at, heartbeat_at + lease_duration(), heartbeat_at, task_id),
            )
            row = cur.fetchone()
            cur.execute(
                """
                update taskhub_resource_leases
                set expires_at = %s, updated_at = %s
                where task_id = %s and lease_token = %s
                """,
                (heartbeat_at + lease_duration(), heartbeat_at, task_id, uuid.UUID(request.lease_token)),
            )
        conn.commit()
    return {"task_id": str(task_id), "lease_expires_at": row["lease_expires_at"]}


@router.post("/tasks/{task_id}/complete")
def complete_task(task_id: uuid.UUID, request: TaskComplete) -> dict[str, Any]:
    completed_at = now_utc()
    with connect() as conn:
        with conn.cursor() as cur:
            task = load_task_for_update(cur, task_id)
            assert_transition(task["state"], "succeeded")
            assert_lease_owner(task, request.worker_id, request.lease_token)
            validation = validate_task_result(task["type"], request.result)
            if validation["strict"] and not validation["valid"]:
                contract_error = {"code": "HANDOFF_CONTRACT_REJECTED", "errors": validation["errors"]}
                cur.execute(
                    """
                    update taskhub_tasks set state = 'failed', error = %s, lease_token = null,
                        lease_expires_at = null, updated_at = %s where id = %s
                    """,
                    (Jsonb(contract_error), completed_at, task_id),
                )
                cur.execute(
                    """
                    update taskhub_task_handoffs set status = 'rejected', payload = %s,
                        payload_hash = %s, validation_errors = %s, updated_at = %s where task_id = %s
                    """,
                    (Jsonb(redact(request.result)), canonical_hash(request.result),
                     Jsonb(validation["errors"]), completed_at, task_id),
                )
                release_resource_leases(cur, task_id)
                add_history(cur, task_id, "running", "failed", request.worker_id, "handoff contract rejected", contract_error)
                reconcile_workflow_tasks(cur, task, request.worker_id)
                conn.commit()
                raise HTTPException(status_code=422, detail={"message": "task result violates handoff contract", **validation})
            cur.execute(
                """
                update taskhub_tasks
                set state = 'succeeded', result = %s, error = null, completed_at = %s,
                    lease_token = null, lease_expires_at = null, updated_at = %s
                where id = %s
                returning *
                """,
                (Jsonb(redact(request.result)), completed_at, completed_at, task_id),
            )
            row = cur.fetchone()
            cur.execute(
                """
                update taskhub_task_handoffs set status = 'validated', payload = %s,
                    payload_hash = %s, validation_errors = '[]'::jsonb, updated_at = %s where task_id = %s
                """,
                (Jsonb(redact(request.result)), canonical_hash(request.result), completed_at, task_id),
            )
            release_resource_leases(cur, task_id)
            add_history(cur, task_id, task["state"], "succeeded", request.worker_id, "task completed", request.result)
            reconcile_workflow_tasks(cur, task, request.worker_id)
        conn.commit()
    return serialize_task(row)


@router.post("/tasks/{task_id}/fail")
def fail_task(task_id: uuid.UUID, request: TaskFail) -> dict[str, Any]:
    updated_at = now_utc()
    with connect() as conn:
        with conn.cursor() as cur:
            task = load_task_for_update(cur, task_id)
            assert_transition(task["state"], "failed")
            assert_lease_owner(task, request.worker_id, request.lease_token)
            cur.execute(
                """
                update taskhub_tasks
                set state = 'failed', error = %s, lease_token = null, lease_expires_at = null, updated_at = %s
                where id = %s
                returning *
                """,
                (Jsonb(redact(request.error)), updated_at, task_id),
            )
            row = cur.fetchone()
            release_resource_leases(cur, task_id)
            add_history(cur, task_id, task["state"], "failed", request.worker_id, "task failed", request.error)
            recovery = schedule_automatic_rework(cur, row, request.error, request.worker_id)
            if recovery:
                row = recovery["task"]
            else:
                reconcile_workflow_tasks(cur, task, request.worker_id)
        conn.commit()
    return serialize_task(row)


@router.post("/tasks/{task_id}/retry")
def retry_task(task_id: uuid.UUID, request: Request, payload: TaskRetry) -> dict[str, Any]:
    updated_at = now_utc()
    with connect() as conn:
        with conn.cursor() as cur:
            task = load_task_for_update(cur, task_id)
            assert_transition(task["state"], "pending")
            retry_input = payload.input if payload.input is not None else task.get("input") or {}
            try:
                validate_task_input(task["type"], retry_input)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            release_resource_leases(cur, task_id)
            cur.execute(
                """
                update taskhub_tasks
                set state = 'pending',
                    input = %s,
                    error = null,
                    worker_id = null,
                    lease_token = null,
                    lease_expires_at = null,
                    heartbeat_at = null,
                    claimed_at = null,
                    completed_at = null,
                    retry_count = retry_count + 1,
                    updated_at = %s
                where id = %s
                returning *
                """,
                (Jsonb(redact(retry_input)), updated_at, task_id),
            )
            row = cur.fetchone()
            add_history(
                cur, task_id, task["state"], "pending", request.state.actor, payload.reason,
                {"input_updated": payload.input is not None},
            )
            requeued_descendants = requeue_dependency_blocked_descendants(cur, task_id, request.state.actor)
            workflow_id = workflow_id_for_task(task)
            if workflow_id:
                cur.execute("select state from taskhub_workflows where id = %s", (workflow_id,))
                workflow = cur.fetchone()
                if workflow and workflow["state"] == "blocked":
                    transition_workflow(
                        cur, workflow_id, "retry", request.state.actor, payload.reason,
                        {"retry_task_id": str(task_id), "requeued_descendants": requeued_descendants},
                    )
        conn.commit()
    return serialize_task(row)


@router.post("/tasks/{task_id}/block")
def block_task(task_id: uuid.UUID, request: Request, payload: TaskAction) -> dict[str, Any]:
    updated_at = now_utc()
    with connect() as conn:
        with conn.cursor() as cur:
            task = load_task_for_update(cur, task_id)
            assert_transition(task["state"], "blocked")
            release_resource_leases(cur, task_id)
            cur.execute(
                """
                update taskhub_tasks
                set state = 'blocked', error = %s, worker_id = null, lease_token = null,
                    lease_expires_at = null, heartbeat_at = null, updated_at = %s
                where id = %s
                returning *
                """,
                (Jsonb({"blocked_reason": payload.reason}), updated_at, task_id),
            )
            row = cur.fetchone()
            add_history(cur, task_id, task["state"], "blocked", request.state.actor, payload.reason)
            workflow_id = workflow_id_for_task(task)
            if workflow_id and task["type"] == "review.human":
                transition_workflow(cur, workflow_id, "block", request.state.actor, payload.reason)
            else:
                reconcile_workflow_tasks(cur, task, request.state.actor)
        conn.commit()
    return serialize_task(row)


@router.post("/tasks/{task_id}/approve")
def approve_task(task_id: uuid.UUID, request: Request, payload: TaskApprove) -> dict[str, Any]:
    completed_at = now_utc()
    with connect() as conn:
        with conn.cursor() as cur:
            task = load_task_for_update(cur, task_id)
            assert_human_approval(task)
            current_plan = (task.get("input") or {}).get("approval_plan", [])
            expected_hash = task.get("approval_content_hash") or canonical_hash(current_plan)
            if payload.approval_hash != expected_hash:
                raise HTTPException(status_code=409, detail="approval content changed; reload before approving")
            approved_plan = payload.approval_plan if payload.approval_plan is not None else current_plan
            approved_plan_hash = canonical_hash(approved_plan)
            downstream_specs = downstream_tasks_for_approval(task, payload.approval_plan)
            downstream_rows = []
            for index, spec in enumerate(downstream_specs):
                row = insert_task(cur, spec, request.state.actor, f"created after approval: {task_id}")
                plan_item = approved_plan[index]
                requested_indices = plan_item.get("depends_on_indices") if isinstance(plan_item, dict) else None
                parent_indices = requested_indices if isinstance(requested_indices, list) else ([index - 1] if index else [])
                for parent_index in parent_indices:
                    if not isinstance(parent_index, int) or parent_index < 0 or parent_index >= index:
                        raise HTTPException(status_code=400, detail=f"approval_plan[{index}] has invalid dependency index")
                    add_task_dependency(cur, row["id"], downstream_rows[parent_index]["id"])
                downstream_rows.append(row)
            approval_result = {
                "approved": True,
                "reason": payload.reason,
                "source_approval_hash": expected_hash,
                "approved_content_hash": approved_plan_hash,
                "edited_approval_plan": payload.approval_plan is not None,
                "approval_plan": approved_plan,
                "downstream_created": [
                    {
                        "id": str(row["id"]),
                        "type": row["type"],
                        "title": row["title"],
                        "state": row["state"],
                        "priority": row["priority"],
                        "depends_on": (
                            approved_plan[index].get("depends_on_indices", [index - 1] if index else [])
                            if isinstance(approved_plan[index], dict)
                            else []
                        ),
                    }
                    for index, row in enumerate(downstream_rows)
                ],
                **payload.result,
            }
            cur.execute(
                """
                update taskhub_tasks
                set state = 'succeeded',
                    result = %s,
                    error = null,
                    worker_id = %s,
                    completed_at = %s,
                    updated_at = %s
                where id = %s
                returning *
                """,
                (
                    Jsonb(redact(approval_result)),
                    request.state.actor,
                    completed_at,
                    completed_at,
                    task_id,
                ),
            )
            row = cur.fetchone()
            add_history(cur, task_id, task["state"], "succeeded", request.state.actor, payload.reason, approval_result)
            workflow_id = workflow_id_for_task(task)
            if workflow_id:
                transition_workflow(
                    cur,
                    workflow_id,
                    "approve_plan",
                    request.state.actor,
                    payload.reason,
                    {"approval_task_id": str(task_id), "approved_content_hash": approved_plan_hash},
                )
                if not downstream_rows:
                    transition_workflow(
                        cur, workflow_id, "implementation_done", request.state.actor,
                        "approved plan contains no implementation tasks", {"task_states": {}},
                    )
        conn.commit()
    return serialize_task(row)


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: uuid.UUID, request: Request, payload: TaskAction) -> dict[str, Any]:
    updated_at = now_utc()
    with connect() as conn:
        with conn.cursor() as cur:
            task = load_task_for_update(cur, task_id)
            assert_transition(task["state"], "canceled")
            release_resource_leases(cur, task_id)
            cur.execute(
                """
                update taskhub_tasks
                set state = 'canceled', updated_at = %s
                where id = %s
                returning *
                """,
                (updated_at, task_id),
            )
            row = cur.fetchone()
            add_history(cur, task_id, task["state"], "canceled", request.state.actor, payload.reason)
            workflow_id = workflow_id_for_task(task)
            if workflow_id and task["type"] == "review.human":
                transition_workflow(cur, workflow_id, "cancel", request.state.actor, payload.reason)
            else:
                reconcile_workflow_tasks(cur, task, request.state.actor)
        conn.commit()
    return serialize_task(row)
