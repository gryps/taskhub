from __future__ import annotations

import os
import re
import uuid
from pathlib import PurePosixPath
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from app.taskhub import canonical_hash, connect, now_utc, record_audit_event, redact


router = APIRouter(prefix="/taskhub/advanced", tags=["advanced"])

PROJECT_STATES = {"active", "paused", "archived"}
MEMORY_KINDS = {"decision", "constraint", "lesson", "summary", "risk"}
AUTONOMY_ACTIONS = {"retry_task", "requeue_expired_lease", "run_quality_evaluation", "refresh_context"}
HUMAN_ONLY_ACTIONS = {"release", "publish", "credential_change", "delete_project", "production_apply"}
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,79}$")


class ProjectCreate(BaseModel):
    slug: str = Field(..., min_length=2, max_length=80)
    name: str = Field(..., min_length=1, max_length=120)
    source_host: str = Field(..., min_length=1, max_length=255)
    source_root: str = Field(..., min_length=1, max_length=500)
    default_branch: str = Field("main", min_length=1, max_length=200)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    state: str | None = None
    default_branch: str | None = Field(default=None, min_length=1, max_length=200)
    metadata: dict[str, Any] | None = None


class WorkspaceCreate(BaseModel):
    pipeline_id: uuid.UUID | None = None
    worker_id: str = Field(..., min_length=1, max_length=200)
    workspace_id: str = Field(..., min_length=1, max_length=200)
    workspace_path: str = Field(..., min_length=1, max_length=500)
    branch: str = Field(..., min_length=1, max_length=200)
    baseline_commit: str | None = Field(default=None, max_length=64)


class SnapshotCreate(BaseModel):
    branch: str = Field(..., min_length=1, max_length=200)
    commit: str = Field(..., min_length=7, max_length=64)
    dirty: bool = False
    status_hash: str | None = Field(default=None, max_length=64)
    change_count: int = Field(0, ge=0)
    context: dict[str, Any] = Field(default_factory=dict)


class MemoryCreate(BaseModel):
    kind: str
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1, max_length=12000)
    importance: int = Field(50, ge=1, le=100)
    tags: list[str] = Field(default_factory=list, max_length=20)
    source_workflow_id: uuid.UUID | None = None


class MemoryUpdate(BaseModel):
    active: bool


class UsageCreate(BaseModel):
    project: str
    workflow_id: uuid.UUID | None = None
    role: str
    provider: str
    model: str = ""
    status: str = "succeeded"
    prompt_tokens: int = Field(0, ge=0)
    completion_tokens: int = Field(0, ge=0)
    estimated_cost_usd: float = Field(0, ge=0)


class BudgetUpdate(BaseModel):
    monthly_budget_usd: float = Field(0, ge=0)
    warning_ratio: float = Field(0.8, gt=0, le=1)
    hard_limit: bool = False


class GovernanceUpdate(BaseModel):
    autonomy_enabled: bool = False
    allowed_actions: list[str] = Field(default_factory=lambda: sorted(AUTONOMY_ACTIONS))
    max_risk: str = "low"
    require_human_release: bool = True
    require_human_publish: bool = True


class GovernanceCheck(BaseModel):
    project: str
    action: str
    risk_level: str = "low"
    entity_type: str = "request"
    entity_id: str = "manual"
    context: dict[str, Any] = Field(default_factory=dict)


def init_advanced() -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                create table if not exists taskhub_projects (
                    id uuid primary key, slug text not null unique, name text not null,
                    state text not null check (state in ('active', 'paused', 'archived')),
                    source_host text not null, source_root text not null, default_branch text not null,
                    metadata jsonb not null default '{}'::jsonb,
                    created_at timestamptz not null, updated_at timestamptz not null,
                    unique (source_host, source_root)
                )
                """
            )
            cur.execute(
                """
                create table if not exists taskhub_project_workspaces (
                    id uuid primary key, project_id uuid not null references taskhub_projects(id) on delete cascade,
                    pipeline_id uuid references taskhub_pipelines(id) on delete set null,
                    worker_id text not null, workspace_id text not null unique, workspace_path text not null,
                    branch text not null, baseline_commit text, state text not null default 'ready',
                    last_sync_at timestamptz, created_at timestamptz not null, updated_at timestamptz not null,
                    unique (project_id, worker_id, workspace_path), unique (project_id, branch)
                )
                """
            )
            cur.execute(
                """
                create table if not exists taskhub_context_snapshots (
                    id uuid primary key, project_id uuid not null references taskhub_projects(id) on delete cascade,
                    version integer not null, branch text not null, commit text not null, dirty boolean not null,
                    status_hash text, change_count integer not null default 0, context jsonb not null default '{}'::jsonb,
                    content_hash text not null, created_by text not null, created_at timestamptz not null,
                    unique (project_id, version), unique (project_id, content_hash)
                )
                """
            )
            cur.execute(
                """
                create table if not exists taskhub_memories (
                    id uuid primary key, project_id uuid not null references taskhub_projects(id) on delete cascade,
                    kind text not null, title text not null, content text not null, importance integer not null,
                    tags text[] not null default '{}'::text[], source_workflow_id uuid,
                    content_hash text not null, active boolean not null default true,
                    created_by text not null, created_at timestamptz not null, updated_at timestamptz not null,
                    unique (project_id, kind, content_hash)
                )
                """
            )
            cur.execute(
                """
                create table if not exists taskhub_model_usage (
                    id uuid primary key, project text not null, workflow_id uuid, role text not null,
                    provider text not null, model text not null, status text not null,
                    prompt_tokens integer not null default 0, completion_tokens integer not null default 0,
                    estimated_cost_usd numeric(12,6) not null default 0, created_at timestamptz not null
                )
                """
            )
            cur.execute(
                """
                create table if not exists taskhub_project_budgets (
                    project text primary key, monthly_budget_usd numeric(12,2) not null default 0,
                    warning_ratio numeric(4,3) not null default 0.8, hard_limit boolean not null default false,
                    updated_by text not null, updated_at timestamptz not null
                )
                """
            )
            cur.execute(
                """
                create table if not exists taskhub_quality_evaluations (
                    id uuid primary key, project text not null, workflow_id uuid,
                    score integer not null, verdict text not null, metrics jsonb not null,
                    evidence_hash text not null, created_by text not null, created_at timestamptz not null,
                    unique (workflow_id, evidence_hash)
                )
                """
            )
            cur.execute(
                """
                create table if not exists taskhub_governance_policies (
                    project text primary key, autonomy_enabled boolean not null default false,
                    allowed_actions text[] not null default '{}'::text[], max_risk text not null default 'low',
                    require_human_release boolean not null default true,
                    require_human_publish boolean not null default true,
                    updated_by text not null, updated_at timestamptz not null
                )
                """
            )
            cur.execute(
                """
                create table if not exists taskhub_governance_decisions (
                    id uuid primary key, project text not null, action text not null, risk_level text not null,
                    entity_type text not null, entity_id text not null, decision text not null,
                    reason text not null, context jsonb not null default '{}'::jsonb,
                    actor text not null, created_at timestamptz not null
                )
                """
            )
            for sql in (
                "create index if not exists idx_taskhub_context_project on taskhub_context_snapshots(project_id, version desc)",
                "create index if not exists idx_taskhub_memory_project on taskhub_memories(project_id, active, importance desc)",
                "create index if not exists idx_taskhub_usage_project on taskhub_model_usage(project, created_at desc)",
                "create index if not exists idx_taskhub_quality_project on taskhub_quality_evaluations(project, created_at desc)",
                "create index if not exists idx_taskhub_governance_project on taskhub_governance_decisions(project, created_at desc)",
            ):
                cur.execute(sql)
        conn.commit()


def allowed_source_hosts() -> set[str]:
    return {item.strip() for item in os.getenv("TASKHUB_PROJECT_SOURCE_HOSTS", "192.168.31.17").split(",") if item.strip()}


def validate_project_location(slug: str, source_host: str, source_root: str) -> tuple[str, str, str]:
    slug = slug.strip().lower()
    host = source_host.strip().lower()
    root = source_root.strip()
    if not SLUG_PATTERN.fullmatch(slug):
        raise HTTPException(status_code=400, detail="invalid project slug")
    if host not in allowed_source_hosts():
        raise HTTPException(status_code=400, detail="source host is not allowlisted")
    path = PurePosixPath(root)
    if not path.is_absolute() or ".." in path.parts or any(char in root for char in "\n\r\0"):
        raise HTTPException(status_code=400, detail="source root must be a safe absolute path")
    return slug, host, str(path)


def project_row(cur, slug: str, lock: bool = False) -> dict[str, Any]:
    cur.execute(f"select * from taskhub_projects where slug = %s{' for update' if lock else ''}", (slug,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="project not found")
    return row


@router.post("/projects", status_code=201)
def create_project(request: Request, payload: ProjectCreate) -> dict[str, Any]:
    slug, host, root = validate_project_location(payload.slug, payload.source_host, payload.source_root)
    timestamp = now_utc()
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """insert into taskhub_projects
                    (id, slug, name, state, source_host, source_root, default_branch, metadata, created_at, updated_at)
                    values (%s,%s,%s,'active',%s,%s,%s,%s,%s,%s) returning *""",
                    (uuid.uuid4(), slug, payload.name.strip(), host, root, payload.default_branch.strip(),
                     Jsonb(redact(payload.metadata)), timestamp, timestamp),
                )
                row = cur.fetchone()
            conn.commit()
    except Exception as exc:
        if exc.__class__.__name__ == "UniqueViolation":
            raise HTTPException(status_code=409, detail="project or source location already exists") from exc
        raise
    record_audit_event(request.state.actor, "advanced.project.create", 201, detail={"project": slug})
    return row


@router.get("/projects")
def list_projects() -> list[dict[str, Any]]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """select project.*,
                (select count(*) from taskhub_project_workspaces workspace where workspace.project_id=project.id) workspace_count,
                (select max(version) from taskhub_context_snapshots snapshot where snapshot.project_id=project.id) context_version,
                (select count(*) from taskhub_memories memory where memory.project_id=project.id and memory.active) memory_count
                from taskhub_projects project order by project.updated_at desc"""
            )
            return cur.fetchall()


@router.get("/projects/{slug}")
def get_project(slug: str) -> dict[str, Any]:
    with connect() as conn:
        with conn.cursor() as cur:
            project = project_row(cur, slug)
            cur.execute("select * from taskhub_project_workspaces where project_id=%s order by created_at", (project["id"],))
            workspaces = cur.fetchall()
            cur.execute("select * from taskhub_context_snapshots where project_id=%s order by version desc limit 20", (project["id"],))
            snapshots = cur.fetchall()
    return {**project, "workspaces": workspaces, "snapshots": snapshots}


@router.patch("/projects/{slug}")
def update_project(slug: str, request: Request, payload: ProjectUpdate) -> dict[str, Any]:
    values = payload.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(status_code=400, detail="no project fields supplied")
    if values.get("state") not in PROJECT_STATES and "state" in values:
        raise HTTPException(status_code=400, detail="invalid project state")
    assignments, params = [], []
    for key in ("name", "state", "default_branch"):
        if key in values:
            assignments.append(f"{key}=%s")
            params.append(str(values[key]).strip())
    if "metadata" in values:
        assignments.append("metadata=%s")
        params.append(Jsonb(redact(values["metadata"] or {})))
    assignments.append("updated_at=%s")
    params.extend([now_utc(), slug])
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"update taskhub_projects set {', '.join(assignments)} where slug=%s returning *", params)
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="project not found")
        conn.commit()
    record_audit_event(request.state.actor, "advanced.project.update", 200, detail={"project": slug})
    return row


@router.post("/projects/{slug}/workspaces", status_code=201)
def create_workspace(slug: str, request: Request, payload: WorkspaceCreate) -> dict[str, Any]:
    path = PurePosixPath(payload.workspace_path.strip())
    if not path.is_absolute() or ".." in path.parts:
        raise HTTPException(status_code=400, detail="workspace path must be absolute")
    timestamp = now_utc()
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                project = project_row(cur, slug)
                if payload.pipeline_id:
                    cur.execute("select project, workspace_id from taskhub_pipelines where id=%s", (payload.pipeline_id,))
                    pipeline = cur.fetchone()
                    if not pipeline or pipeline["project"] != slug or pipeline["workspace_id"] != payload.workspace_id:
                        raise HTTPException(status_code=400, detail="pipeline project/workspace does not match")
                cur.execute(
                    """insert into taskhub_project_workspaces
                    (id,project_id,pipeline_id,worker_id,workspace_id,workspace_path,branch,baseline_commit,state,created_at,updated_at)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,'ready',%s,%s) returning *""",
                    (uuid.uuid4(), project["id"], payload.pipeline_id, payload.worker_id.strip(), payload.workspace_id.strip(),
                     str(path), payload.branch.strip(), payload.baseline_commit, timestamp, timestamp),
                )
                row = cur.fetchone()
            conn.commit()
    except Exception as exc:
        if exc.__class__.__name__ == "UniqueViolation":
            raise HTTPException(status_code=409, detail="workspace path, ID, or branch already registered") from exc
        raise
    record_audit_event(request.state.actor, "advanced.workspace.create", 201, detail={"project": slug, "workspace": payload.workspace_id})
    return row


@router.post("/projects/{slug}/snapshots", status_code=201)
def create_snapshot(slug: str, request: Request, payload: SnapshotCreate) -> dict[str, Any]:
    content = payload.model_dump()
    digest = canonical_hash(content)
    timestamp = now_utc()
    with connect() as conn:
        with conn.cursor() as cur:
            project = project_row(cur, slug, lock=True)
            cur.execute("select * from taskhub_context_snapshots where project_id=%s and content_hash=%s", (project["id"], digest))
            existing = cur.fetchone()
            if existing:
                return existing
            cur.execute("select coalesce(max(version),0)+1 next_version from taskhub_context_snapshots where project_id=%s", (project["id"],))
            version = cur.fetchone()["next_version"]
            cur.execute(
                """insert into taskhub_context_snapshots
                (id,project_id,version,branch,commit,dirty,status_hash,change_count,context,content_hash,created_by,created_at)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning *""",
                (uuid.uuid4(), project["id"], version, payload.branch.strip(), payload.commit.strip(), payload.dirty,
                 payload.status_hash, payload.change_count, Jsonb(redact(payload.context)), digest, request.state.actor, timestamp),
            )
            row = cur.fetchone()
        conn.commit()
    return row


@router.post("/projects/{slug}/memories", status_code=201)
def create_memory(slug: str, request: Request, payload: MemoryCreate) -> dict[str, Any]:
    if payload.kind not in MEMORY_KINDS:
        raise HTTPException(status_code=400, detail="invalid memory kind")
    tags = sorted({tag.strip().lower() for tag in payload.tags if tag.strip()})
    if any(len(tag) > 60 for tag in tags):
        raise HTTPException(status_code=400, detail="memory tag is too long")
    digest = canonical_hash({"title": payload.title.strip(), "content": payload.content.strip()})
    timestamp = now_utc()
    with connect() as conn:
        with conn.cursor() as cur:
            project = project_row(cur, slug)
            cur.execute(
                """insert into taskhub_memories
                (id,project_id,kind,title,content,importance,tags,source_workflow_id,content_hash,created_by,created_at,updated_at)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict (project_id,kind,content_hash) do update set active=true, importance=excluded.importance,
                tags=excluded.tags, updated_at=excluded.updated_at returning *""",
                (uuid.uuid4(), project["id"], payload.kind, payload.title.strip(), payload.content.strip(), payload.importance,
                 tags, payload.source_workflow_id, digest, request.state.actor, timestamp, timestamp),
            )
            row = cur.fetchone()
        conn.commit()
    return row


@router.patch("/projects/{slug}/memories/{memory_id}")
def update_memory(slug: str, memory_id: uuid.UUID, request: Request, payload: MemoryUpdate) -> dict[str, Any]:
    with connect() as conn:
        with conn.cursor() as cur:
            project = project_row(cur, slug)
            cur.execute(
                """update taskhub_memories set active=%s,updated_at=%s
                where id=%s and project_id=%s returning *""",
                (payload.active, now_utc(), memory_id, project["id"]),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="memory not found")
        conn.commit()
    record_audit_event(request.state.actor, "advanced.memory.update", 200, detail={"project": slug, "memory_id": str(memory_id)})
    return row


@router.get("/projects/{slug}/context-pack")
def context_pack(slug: str, memory_limit: int = Query(20, ge=1, le=50)) -> dict[str, Any]:
    return get_context_pack(slug, memory_limit)


def get_context_pack(slug: str, memory_limit: int = 20) -> dict[str, Any]:
    with connect() as conn:
        with conn.cursor() as cur:
            project = project_row(cur, slug)
            cur.execute("select * from taskhub_context_snapshots where project_id=%s order by version desc limit 1", (project["id"],))
            snapshot = cur.fetchone()
            cur.execute(
                "select kind,title,content,importance,tags,created_at from taskhub_memories where project_id=%s and active order by importance desc, created_at desc limit %s",
                (project["id"], memory_limit),
            )
            memories = cur.fetchall()
    pack = {"project": {key: project[key] for key in ("slug", "name", "source_host", "source_root", "default_branch")},
            "snapshot": snapshot, "memories": memories}
    return {**pack, "pack_hash": canonical_hash(pack)}


def context_for_prompt(slug: str, max_chars: int = 6000) -> str:
    """Return bounded project context; unregistered projects remain usable."""
    try:
        pack = get_context_pack(slug, 12)
    except Exception:
        return ""
    lines = [
        f"Authority: {pack['project']['source_host']}:{pack['project']['source_root']}",
        f"Default branch: {pack['project']['default_branch']}",
    ]
    snapshot = pack.get("snapshot") or {}
    if snapshot:
        lines.append(f"Context baseline: v{snapshot.get('version')} {snapshot.get('branch')}@{snapshot.get('commit')}")
    for memory in pack.get("memories") or []:
        lines.append(f"[{memory['kind']}] {memory['title']}: {memory['content']}")
    return "\n".join(lines)[:max_chars]


def record_model_usage(project: str, workflow_id: uuid.UUID | str | None, role: str, provider: str, model: str,
                       status: str, usage: dict[str, Any] | None = None, estimated_cost_usd: float = 0) -> None:
    usage = usage or {}
    prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    if not estimated_cost_usd:
        prefix = re.sub(r"[^A-Z0-9]", "_", provider.upper())
        input_rate = float(os.getenv(f"LLM_COST_{prefix}_INPUT_PER_MILLION", "0"))
        output_rate = float(os.getenv(f"LLM_COST_{prefix}_OUTPUT_PER_MILLION", "0"))
        estimated_cost_usd = (prompt * input_rate + completion * output_rate) / 1_000_000
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """insert into taskhub_model_usage
                    (id,project,workflow_id,role,provider,model,status,prompt_tokens,completion_tokens,estimated_cost_usd,created_at)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (uuid.uuid4(), project, workflow_id or None, role, provider, model or "", status,
                     prompt, completion, estimated_cost_usd, now_utc()),
                )
            conn.commit()
    except Exception:
        return


@router.get("/usage")
def usage_summary(project: str, days: int = Query(30, ge=1, le=366)) -> dict[str, Any]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """select provider,model,role,count(*) calls,
                sum(prompt_tokens) prompt_tokens,sum(completion_tokens) completion_tokens,
                sum(estimated_cost_usd)::float estimated_cost_usd,
                count(*) filter(where status<>'succeeded') failed_calls
                from taskhub_model_usage where project=%s and created_at >= now()-(%s || ' days')::interval
                group by provider,model,role order by calls desc""", (project, days))
            rows = cur.fetchall()
            cur.execute("select * from taskhub_project_budgets where project=%s", (project,))
            budget = cur.fetchone()
            cur.execute(
                "select coalesce(sum(estimated_cost_usd),0)::float spent from taskhub_model_usage where project=%s and created_at >= date_trunc('month', now())",
                (project,),
            )
            month_spent = float(cur.fetchone()["spent"] or 0)
    spent = sum(float(row["estimated_cost_usd"] or 0) for row in rows)
    limit = float((budget or {}).get("monthly_budget_usd") or 0)
    return {"project": project, "days": days, "groups": rows, "spent_usd": spent, "month_spent_usd": month_spent, "budget": budget,
            "budget_status": "unlimited" if not limit else ("exceeded" if month_spent >= limit else "warning" if month_spent >= limit * float(budget["warning_ratio"]) else "ok")}


def model_budget_allows(project: str) -> tuple[bool, str]:
    try:
        summary = usage_summary(project, 31)
    except Exception:
        return True, "budget unavailable"
    budget = summary.get("budget") or {}
    if budget.get("hard_limit") and summary["budget_status"] == "exceeded":
        return False, "project model budget hard limit reached"
    return True, summary["budget_status"]


@router.get("/scheduler")
def scheduler_status(project: str) -> dict[str, Any]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """select target_worker_id worker_id,
                count(*) filter(where state='running') running,
                count(*) filter(where state='pending') pending,
                count(*) filter(where state='failed') failed
                from taskhub_tasks where project=%s and target_worker_id is not null
                group by target_worker_id order by target_worker_id""", (project,)
            )
            workers = cur.fetchall()
    return {"project": project, "strategy": "least_running_then_pending", "workers": workers,
            "implementation_pool": [item.strip() for item in os.getenv(
                "TASKHUB_IMPLEMENTATION_WORKERS",
                "worker-31-31-implementation-a,worker-31-31-implementation",
            ).split(",") if item.strip()]}


@router.put("/usage/{project}/budget")
def update_budget(project: str, request: Request, payload: BudgetUpdate) -> dict[str, Any]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """insert into taskhub_project_budgets(project,monthly_budget_usd,warning_ratio,hard_limit,updated_by,updated_at)
                values (%s,%s,%s,%s,%s,%s) on conflict(project) do update set monthly_budget_usd=excluded.monthly_budget_usd,
                warning_ratio=excluded.warning_ratio,hard_limit=excluded.hard_limit,updated_by=excluded.updated_by,updated_at=excluded.updated_at returning *""",
                (project, payload.monthly_budget_usd, payload.warning_ratio, payload.hard_limit, request.state.actor, now_utc()),
            )
            row = cur.fetchone()
        conn.commit()
    return row


def evaluate_workflow(project: str, workflow_id: uuid.UUID, actor: str) -> dict[str, Any]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("select * from taskhub_workflows where id=%s and project=%s", (workflow_id, project))
            workflow = cur.fetchone()
            if not workflow:
                raise HTTPException(status_code=404, detail="workflow not found")
            cur.execute(
                """select task.state,task.type,handoff.status handoff_status from taskhub_tasks task
                left join taskhub_task_handoffs handoff on handoff.task_id=task.id
                where task.metadata->>'workflow_id'=%s""", (str(workflow_id),))
            tasks = cur.fetchall()
            total = len(tasks)
            succeeded = sum(row["state"] == "succeeded" for row in tasks)
            failed = sum(row["state"] in {"failed", "blocked", "canceled"} for row in tasks)
            validated = sum(row.get("handoff_status") == "validated" for row in tasks if row.get("handoff_status"))
            handoffs = sum(bool(row.get("handoff_status")) for row in tasks)
            metrics = {"task_count": total, "succeeded": succeeded, "failed_or_blocked": failed,
                       "validated_handoffs": validated, "handoff_count": handoffs, "workflow_state": workflow["state"]}
            score, verdict = quality_score(metrics)
            digest = canonical_hash(metrics)
            cur.execute(
                """insert into taskhub_quality_evaluations
                (id,project,workflow_id,score,verdict,metrics,evidence_hash,created_by,created_at)
                values(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict(workflow_id,evidence_hash) do update set score=excluded.score returning *""",
                (uuid.uuid4(), project, workflow_id, score, verdict, Jsonb(metrics), digest, actor, now_utc()),
            )
            row = cur.fetchone()
        conn.commit()
    return row


@router.post("/quality/{workflow_id}/evaluate")
def run_quality_evaluation(workflow_id: uuid.UUID, project: str, request: Request) -> dict[str, Any]:
    return evaluate_workflow(project, workflow_id, request.state.actor)


@router.get("/quality")
def list_quality(project: str, limit: int = Query(50, ge=1, le=200)) -> list[dict[str, Any]]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("select * from taskhub_quality_evaluations where project=%s order by created_at desc limit %s", (project, limit))
            return cur.fetchall()


def governance_policy(cur, project: str) -> dict[str, Any]:
    cur.execute("select * from taskhub_governance_policies where project=%s", (project,))
    row = cur.fetchone()
    return row or {"project": project, "autonomy_enabled": False, "allowed_actions": sorted(AUTONOMY_ACTIONS),
                   "max_risk": "low", "require_human_release": True, "require_human_publish": True}


def quality_score(metrics: dict[str, Any]) -> tuple[int, str]:
    total = int(metrics.get("task_count") or 0)
    succeeded = int(metrics.get("succeeded") or 0)
    failed = int(metrics.get("failed_or_blocked") or 0)
    handoffs = int(metrics.get("handoff_count") or 0)
    validated = int(metrics.get("validated_handoffs") or 0)
    score = max(0, min(100, round(
        (succeeded / max(total, 1)) * 70
        + (validated / max(handoffs, 1)) * 20
        + (10 if metrics.get("workflow_state") == "completed" else 0)
        - failed * 10
    )))
    verdict = "pass" if score >= 85 and failed == 0 else "review" if score >= 60 else "fail"
    return score, verdict


def decide_governance(policy: dict[str, Any], action: str, risk_level: str) -> tuple[str, str]:
    risk_order = {"low": 0, "medium": 1, "high": 2}
    if risk_level not in risk_order:
        raise ValueError("invalid risk level")
    if action in HUMAN_ONLY_ACTIONS:
        return "human_required", "action is permanently reserved for human approval"
    if not policy["autonomy_enabled"]:
        return "human_required", "project autonomy is disabled"
    if action not in policy["allowed_actions"]:
        return "denied", "action is not in the autonomous allowlist"
    if risk_order[risk_level] > risk_order[policy["max_risk"]]:
        return "human_required", "risk exceeds autonomous policy"
    return "allowed", "action is within the autonomous policy"


@router.get("/governance/{project}")
def get_governance(project: str) -> dict[str, Any]:
    with connect() as conn:
        with conn.cursor() as cur:
            return governance_policy(cur, project)


@router.put("/governance/{project}")
def update_governance(project: str, request: Request, payload: GovernanceUpdate) -> dict[str, Any]:
    allowed = sorted(set(payload.allowed_actions))
    if not set(allowed).issubset(AUTONOMY_ACTIONS):
        raise HTTPException(status_code=400, detail="allowed_actions contains a non-autonomous action")
    if payload.max_risk not in {"low", "medium"}:
        raise HTTPException(status_code=400, detail="autonomy max_risk must be low or medium")
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """insert into taskhub_governance_policies
                (project,autonomy_enabled,allowed_actions,max_risk,require_human_release,require_human_publish,updated_by,updated_at)
                values(%s,%s,%s,%s,%s,%s,%s,%s) on conflict(project) do update set autonomy_enabled=excluded.autonomy_enabled,
                allowed_actions=excluded.allowed_actions,max_risk=excluded.max_risk,require_human_release=excluded.require_human_release,
                require_human_publish=excluded.require_human_publish,updated_by=excluded.updated_by,updated_at=excluded.updated_at returning *""",
                (project, payload.autonomy_enabled, allowed, payload.max_risk, True, True, request.state.actor, now_utc()),
            )
            row = cur.fetchone()
        conn.commit()
    return row


@router.post("/governance/check")
def check_governance(request: Request, payload: GovernanceCheck) -> dict[str, Any]:
    if payload.risk_level not in {"low", "medium", "high"}:
        raise HTTPException(status_code=400, detail="invalid risk level")
    with connect() as conn:
        with conn.cursor() as cur:
            policy = governance_policy(cur, payload.project)
            decision, reason = decide_governance(policy, payload.action, payload.risk_level)
            cur.execute(
                """insert into taskhub_governance_decisions
                (id,project,action,risk_level,entity_type,entity_id,decision,reason,context,actor,created_at)
                values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning *""",
                (uuid.uuid4(), payload.project, payload.action, payload.risk_level, payload.entity_type,
                 payload.entity_id, decision, reason, Jsonb(redact(payload.context)), request.state.actor, now_utc()),
            )
            row = cur.fetchone()
        conn.commit()
    return {**row, "policy": policy}


@router.get("/dashboard")
def advanced_dashboard(project: str) -> dict[str, Any]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("select count(*) count from taskhub_projects where slug=%s", (project,))
            registered = bool(cur.fetchone()["count"])
            cur.execute("select count(*) count from taskhub_project_workspaces workspace join taskhub_projects project on project.id=workspace.project_id where project.slug=%s", (project,))
            workspaces = cur.fetchone()["count"]
            cur.execute("select count(*) count from taskhub_memories memory join taskhub_projects project on project.id=memory.project_id where project.slug=%s and memory.active", (project,))
            memories = cur.fetchone()["count"]
            cur.execute("select count(*) count from taskhub_quality_evaluations where project=%s", (project,))
            evaluations = cur.fetchone()["count"]
            policy = governance_policy(cur, project)
    usage = usage_summary(project, 30)
    return {"project": project, "project_registered": registered, "workspace_count": workspaces,
            "memory_count": memories, "quality_evaluation_count": evaluations,
            "usage": {"spent_usd": usage["spent_usd"], "budget_status": usage["budget_status"]}, "governance": policy}


def run_advanced_cycle() -> bool:
    """Evaluate one newly completed workflow. This cycle never changes release state."""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """select workflow.id, workflow.project
                from taskhub_workflows workflow
                where workflow.state='completed'
                  and not exists (
                    select 1 from taskhub_quality_evaluations evaluation
                    where evaluation.workflow_id=workflow.id
                  )
                order by workflow.updated_at limit 1"""
            )
            row = cur.fetchone()
    if not row:
        return False
    evaluate_workflow(row["project"], row["id"], "taskhub:quality-cycle")
    return True
