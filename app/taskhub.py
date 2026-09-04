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


router = APIRouter(prefix="/taskhub", tags=["taskhub"])

TASK_STATES = {"pending", "running", "succeeded", "failed", "blocked", "canceled"}
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
    "requirement.split",
    "test.run",
    "h5.inspect",
}


class TaskCreate(BaseModel):
    project: str = Field("douyin-listing-workbench", min_length=1)
    type: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    priority: int = 50
    input: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)
    depends_on: list[uuid.UUID] = Field(default_factory=list)


class TaskClaim(BaseModel):
    worker_id: str = Field(..., min_length=1)
    project: str = Field("douyin-listing-workbench", min_length=1)
    types: list[str] = Field(default_factory=list)


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


class TaskApprove(BaseModel):
    reason: str = Field(..., min_length=1)
    result: dict[str, Any] = Field(default_factory=dict)
    approval_plan: list[dict[str, Any]] | None = None
    approval_hash: str = Field(..., min_length=64, max_length=64)


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
            ):
                cur.execute(statement)
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
            cur.execute("create index if not exists idx_taskhub_audit_created on taskhub_audit_events (created_at desc)")
            cur.execute("create index if not exists idx_taskhub_workflows_project on taskhub_workflows (project, created_at desc)")
            cur.execute("create index if not exists idx_taskhub_evidence_workflow on taskhub_role_evidence (workflow_id, ordinal)")
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


def create_workflow_evidence(project: str, requirement: str, plan: dict[str, Any]) -> dict[str, Any]:
    workflow_id = uuid.uuid4()
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
            cur.execute(
                """
                insert into taskhub_workflows
                    (id, project, requirement_hash, state, summary, risk_level, evidence_root_hash, created_at, updated_at)
                values (%s, %s, %s, 'awaiting_human_approval', %s, %s, %s, %s, %s)
                """,
                (
                    workflow_id,
                    project,
                    requirement_hash,
                    str(plan.get("summary") or ""),
                    str(plan.get("risk_level") or "medium"),
                    previous_hash,
                    created_at,
                    created_at,
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


@router.get("/workflows")
def list_workflows(project: str | None = None, limit: int = Query(50, ge=1, le=200)) -> list[dict[str, Any]]:
    where = "where project = %s" if project else ""
    params: list[Any] = [project] if project else []
    params.append(limit)
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"select * from taskhub_workflows {where} order by created_at desc limit %s", params)
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
    return {**workflow, "evidence": [{**row, "payload": redact(row.get("payload"))} for row in evidence]}


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
    approval_hash = canonical_hash(request.input.get("approval_plan", [])) if request.type == "review.human" else None
    cur.execute(
        """
        insert into taskhub_tasks
            (id, project, type, title, state, priority, input, metadata, idempotency_key,
             approval_content_hash, retry_count, created_at, updated_at)
        values
            (%s, %s, %s, %s, 'pending', %s, %s, %s, %s, %s, 0, %s, %s)
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
            )
        )
    return downstream


@router.get("/tasks")
def list_tasks(
    project: str | None = None,
    state: str | None = None,
    type: str | None = None,
    worker_id: str | None = None,
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
def task_stats(project: str | None = None) -> dict[str, Any]:
    params: list[Any] = []
    where = ""
    if project:
        where = "where project = %s"
        params.append(project)
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
    return {"project": project, "states": counts, "total": sum(counts.values())}


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


@router.post("/claim")
def claim_task(request: TaskClaim) -> dict[str, Any]:
    type_filter = ""
    params: list[Any] = [request.project]
    if request.types:
        type_filter = "and task.type = any(%s)"
        params.append(request.types)

    claimed_at = now_utc()
    lease_token = uuid.uuid4()
    lease_expires_at = claimed_at + lease_duration()
    with connect() as conn:
        with conn.cursor() as cur:
            block_tasks_with_failed_dependencies(cur, request.project, "taskhub:dependency-reconciler")
            cur.execute(
                """
                select * from taskhub_tasks
                where state = 'running' and lease_expires_at < %s
                for update skip locked
                """,
                (claimed_at,),
            )
            for expired in cur.fetchall():
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
                and not exists (
                    select 1
                    from taskhub_task_dependencies dependency
                    join taskhub_tasks parent on parent.id = dependency.depends_on_task_id
                    where dependency.task_id = task.id and parent.state <> 'succeeded'
                )
                order by priority desc, created_at
                for update skip locked
                limit 1
                """,
                params,
            )
            task = cur.fetchone()
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
            add_history(cur, task_id, task["state"], "succeeded", request.worker_id, "task completed", request.result)
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
            add_history(cur, task_id, task["state"], "failed", request.worker_id, "task failed", request.error)
        conn.commit()
    return serialize_task(row)


@router.post("/tasks/{task_id}/retry")
def retry_task(task_id: uuid.UUID, request: Request, payload: TaskAction) -> dict[str, Any]:
    updated_at = now_utc()
    with connect() as conn:
        with conn.cursor() as cur:
            task = load_task_for_update(cur, task_id)
            assert_transition(task["state"], "pending")
            cur.execute(
                """
                update taskhub_tasks
                set state = 'pending',
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
                (updated_at, task_id),
            )
            row = cur.fetchone()
            add_history(cur, task_id, task["state"], "pending", request.state.actor, payload.reason)
        conn.commit()
    return serialize_task(row)


@router.post("/tasks/{task_id}/block")
def block_task(task_id: uuid.UUID, request: Request, payload: TaskAction) -> dict[str, Any]:
    updated_at = now_utc()
    with connect() as conn:
        with conn.cursor() as cur:
            task = load_task_for_update(cur, task_id)
            assert_transition(task["state"], "blocked")
            cur.execute(
                """
                update taskhub_tasks
                set state = 'blocked', error = %s, updated_at = %s
                where id = %s
                returning *
                """,
                (Jsonb({"blocked_reason": payload.reason}), updated_at, task_id),
            )
            row = cur.fetchone()
            add_history(cur, task_id, task["state"], "blocked", request.state.actor, payload.reason)
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
        conn.commit()
    return serialize_task(row)


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: uuid.UUID, request: Request, payload: TaskAction) -> dict[str, Any]:
    updated_at = now_utc()
    with connect() as conn:
        with conn.cursor() as cur:
            task = load_task_for_update(cur, task_id)
            assert_transition(task["state"], "canceled")
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
        conn.commit()
    return serialize_task(row)
