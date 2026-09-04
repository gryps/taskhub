from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shlex
import subprocess
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field
from starlette.responses import FileResponse

from app.advanced import (
    AUTONOMY_ACTIONS,
    decide_governance,
    evaluate_workflow,
    governance_policy,
    validate_project_location,
)
from app.planner import PlannerRequest, multi_invoke
from app.taskhub import TaskAction, connect, now_utc, record_audit_event, redact, retry_task


router = APIRouter(prefix="/taskhub/integration", tags=["integration"])
ARTIFACT_ROOT = Path(os.getenv(
    "TASKHUB_ARTIFACT_ROOT",
    str(Path(__file__).resolve().parent.parent / "data" / "artifacts"),
))


class WorkspaceTemplate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    worker_id: str = Field(..., min_length=1, max_length=200)
    workspace_id: str = Field(..., min_length=1, max_length=200)
    workspace_path: str = Field(..., min_length=1, max_length=500)
    branch: str = Field(..., min_length=1, max_length=200)


class OnboardRequest(BaseModel):
    slug: str = Field(..., min_length=2, max_length=80)
    name: str = Field(..., min_length=1, max_length=120)
    source_host: str = Field(..., min_length=1, max_length=255)
    source_root: str = Field(..., min_length=1, max_length=500)
    default_branch: str = Field("main", min_length=1, max_length=200)
    source_ssh_user: str = Field("gryps", min_length=1, max_length=80)
    source_ssh_port: int = Field(22, ge=1, le=65535)
    workspaces: list[WorkspaceTemplate] = Field(default_factory=list, max_length=8)
    sync_enabled: bool = True
    sync_interval_seconds: int = Field(300, ge=60, le=86400)
    monthly_budget_usd: float = Field(0, ge=0)
    autonomy_enabled: bool = False


class FirstWorkflowRequest(BaseModel):
    requirement: str = Field(..., min_length=3, max_length=20000)
    title: str | None = Field(default=None, max_length=160)
    pipeline_id: uuid.UUID | None = None
    priority: int = Field(70, ge=0, le=1000)


class SyncConfigRequest(BaseModel):
    enabled: bool = True
    ssh_user: str = Field("gryps", min_length=1, max_length=80)
    ssh_port: int = Field(22, ge=1, le=65535)
    interval_seconds: int = Field(300, ge=60, le=86400)


class GovernanceExecuteRequest(BaseModel):
    project: str = Field(..., min_length=1, max_length=80)
    action: str
    entity_id: str = Field(..., min_length=1, max_length=200)
    reason: str = Field("operator requested governed execution", min_length=1, max_length=1000)
    risk_level: str = "low"


def init_integration() -> None:
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """create table if not exists taskhub_source_sync (
                project text primary key, enabled boolean not null default true,
                ssh_user text not null, ssh_port integer not null default 22,
                interval_seconds integer not null default 300, status text not null default 'pending',
                last_error text, last_snapshot_id uuid, last_synced_at timestamptz,
                next_sync_at timestamptz, updated_by text not null, updated_at timestamptz not null)"""
            )
            cur.execute(
                """create table if not exists taskhub_artifacts (
                id uuid primary key, project text not null, task_id uuid,
                worker_id text not null, name text not null, media_type text not null,
                size_bytes bigint not null, sha256 text not null, storage_path text not null,
                source_path text, metadata jsonb not null default '{}'::jsonb,
                created_at timestamptz not null, unique(project, sha256, name))"""
            )
            cur.execute("create index if not exists idx_taskhub_artifacts_project on taskhub_artifacts(project,created_at desc)")
            cur.execute(
                """create table if not exists taskhub_devices (
                worker_id text primary key, url text not null unique, platform text not null default '',
                status text not null, task_types text[] not null default '{}'::text[],
                capabilities jsonb not null default '{}'::jsonb, last_seen_at timestamptz,
                updated_at timestamptz not null)"""
            )
            cur.execute("alter table taskhub_devices drop column if exists listing_paired")
            cur.execute("alter table taskhub_devices drop column if exists listing_agent_mask")
            cur.execute(
                """create table if not exists taskhub_governance_executions (
                id uuid primary key, project text not null, action text not null,
                entity_id text not null, decision text not null, status text not null,
                detail jsonb not null default '{}'::jsonb, actor text not null,
                created_at timestamptz not null, completed_at timestamptz)"""
            )
            cur.execute(
                """create table if not exists taskhub_onboarding_runs (
                id uuid primary key, project text not null, status text not null,
                steps jsonb not null default '[]'::jsonb, actor text not null,
                created_at timestamptz not null, completed_at timestamptz)"""
            )
        conn.commit()


def _safe_source_command(root: str) -> str:
    path = PurePosixPath(root)
    if not path.is_absolute() or ".." in path.parts or any(ch in root for ch in "\n\r\0"):
        raise HTTPException(status_code=400, detail="unsafe source root")
    quoted = shlex.quote(str(path))
    return (
        f"git -C {quoted} rev-parse --is-inside-work-tree && "
        f"git -C {quoted} rev-parse HEAD && "
        f"git -C {quoted} rev-parse --abbrev-ref HEAD && "
        f"git -C {quoted} status --porcelain=v1 --untracked-files=normal"
    )


def read_source_snapshot(host: str, root: str, ssh_user: str = "gryps", ssh_port: int = 22) -> dict[str, Any]:
    validate_project_location("source-check", host, root)
    command = [
        "ssh", "-p", str(ssh_port), "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
        f"{ssh_user}@{host}", _safe_source_command(root),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
    if completed.returncode:
        detail = (completed.stderr or completed.stdout or "source Git check failed")[-1200:].strip()
        raise RuntimeError(detail)
    lines = completed.stdout.splitlines()
    if len(lines) < 3 or lines[0].strip() != "true":
        raise RuntimeError("source path is not a Git worktree")
    commit, branch = lines[1].strip(), lines[2].strip()
    changes = lines[3:]
    status_text = "\n".join(changes)
    return {
        "source_host": host, "source_root": root, "branch": branch, "commit": commit,
        "dirty": bool(changes), "change_count": len(changes),
        "status_hash": hashlib.sha256(status_text.encode()).hexdigest(),
        "verification": "read-only Git status verified on authority host",
    }


def _store_snapshot(cur: Any, project: dict[str, Any], snapshot: dict[str, Any], actor: str) -> dict[str, Any]:
    content = {
        "branch": snapshot["branch"], "commit": snapshot["commit"], "dirty": snapshot["dirty"],
        "status_hash": snapshot["status_hash"], "change_count": snapshot["change_count"],
        "context": {key: value for key, value in snapshot.items() if key not in {"branch", "commit", "dirty", "status_hash", "change_count"}},
    }
    digest = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    cur.execute("select * from taskhub_context_snapshots where project_id=%s and content_hash=%s", (project["id"], digest))
    row = cur.fetchone()
    if row:
        return row
    cur.execute("select coalesce(max(version),0)+1 next_version from taskhub_context_snapshots where project_id=%s", (project["id"],))
    version = cur.fetchone()["next_version"]
    cur.execute(
        """insert into taskhub_context_snapshots
        (id,project_id,version,branch,commit,dirty,status_hash,change_count,context,content_hash,created_by,created_at)
        values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning *""",
        (uuid.uuid4(), project["id"], version, content["branch"], content["commit"], content["dirty"],
         content["status_hash"], content["change_count"], Jsonb(content["context"]), digest, actor, now_utc()),
    )
    return cur.fetchone()


def sync_project_source(project_slug: str, actor: str = "taskhub:source-sync") -> dict[str, Any]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("select * from taskhub_projects where slug=%s", (project_slug,))
            project = cur.fetchone()
            if not project:
                raise HTTPException(status_code=404, detail="project not found")
            cur.execute("select * from taskhub_source_sync where project=%s", (project_slug,))
            config = cur.fetchone() or {"ssh_user": "gryps", "ssh_port": 22, "interval_seconds": 300}
        try:
            snapshot = read_source_snapshot(project["source_host"], project["source_root"], config["ssh_user"], config["ssh_port"])
            with conn.cursor() as cur:
                row = _store_snapshot(cur, project, snapshot, actor)
                cur.execute(
                    """update taskhub_source_sync set status='ok',last_error=null,last_snapshot_id=%s,
                    last_synced_at=%s,next_sync_at=%s+(interval_seconds||' seconds')::interval,updated_at=%s where project=%s""",
                    (row["id"], now_utc(), now_utc(), now_utc(), project_slug),
                )
            conn.commit()
            return {"status": "ok", "snapshot": row}
        except Exception as exc:
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute(
                    """update taskhub_source_sync set status='error',last_error=%s,
                    next_sync_at=%s+(interval_seconds||' seconds')::interval,updated_at=%s where project=%s""",
                    (str(exc)[-1200:], now_utc(), now_utc(), project_slug),
                )
            conn.commit()
            raise


def _worker_urls() -> list[str]:
    return [item.strip().rstrip("/") for item in os.getenv("WORKER_URLS", "").split(",") if item.strip()]


def _health(url: str) -> dict[str, Any]:
    response = httpx.get(f"{url}/health", timeout=4, trust_env=False)
    response.raise_for_status()
    return response.json()


@router.post("/onboarding/preflight")
def onboarding_preflight(payload: OnboardRequest) -> dict[str, Any]:
    slug, host, root = validate_project_location(payload.slug, payload.source_host, payload.source_root)
    steps: list[dict[str, Any]] = []
    try:
        snapshot = read_source_snapshot(host, root, payload.source_ssh_user, payload.source_ssh_port)
        steps.append({"key": "source", "label": "Git 权威源", "status": "ok", "detail": f"{snapshot['branch']}@{snapshot['commit'][:12]}"})
    except Exception as exc:
        snapshot = None
        steps.append({"key": "source", "label": "Git 权威源", "status": "error", "detail": str(exc)[-500:]})
    urls = _worker_urls()
    health_by_worker: dict[str, dict[str, Any]] = {}
    for url in urls:
        try:
            health = _health(url)
            worker_id = str(health.get("worker") or health.get("worker_id") or url)
            health_by_worker[worker_id] = health
        except Exception:
            continue
    for workspace in payload.workspaces:
        ok = workspace.worker_id in health_by_worker
        steps.append({"key": f"worker:{workspace.worker_id}", "label": workspace.name, "status": "ok" if ok else "error", "detail": "节点在线" if ok else "节点未在 WORKER_URLS 中在线"})
    return {"project": slug, "ready": all(step["status"] == "ok" for step in steps), "steps": steps, "snapshot": snapshot}


@router.post("/onboarding/apply")
def onboarding_apply(request: Request, payload: OnboardRequest) -> dict[str, Any]:
    preflight = onboarding_preflight(payload)
    if not preflight["ready"]:
        raise HTTPException(status_code=409, detail={"message": "preflight failed", "steps": preflight["steps"]})
    slug, host, root = validate_project_location(payload.slug, payload.source_host, payload.source_root)
    run_id, timestamp, steps = uuid.uuid4(), now_utc(), list(preflight["steps"])
    with connect() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """insert into taskhub_projects(id,slug,name,state,source_host,source_root,default_branch,metadata,created_at,updated_at)
                    values(%s,%s,%s,'active',%s,%s,%s,%s,%s,%s)
                    on conflict(slug) do update set name=excluded.name,source_host=excluded.source_host,
                    source_root=excluded.source_root,default_branch=excluded.default_branch,updated_at=excluded.updated_at returning *""",
                    (uuid.uuid4(), slug, payload.name.strip(), host, root, payload.default_branch.strip(), Jsonb({"onboarded": True}), timestamp, timestamp),
                )
                project = cur.fetchone()
                steps.append({"key": "project", "label": "项目登记", "status": "ok", "detail": slug})
                snapshot = _store_snapshot(cur, project, preflight["snapshot"], request.state.actor)
                steps.append({"key": "snapshot", "label": "上下文基线", "status": "ok", "detail": f"v{snapshot['version']}"})
                for item in payload.workspaces:
                    path = PurePosixPath(item.workspace_path)
                    if not path.is_absolute() or ".." in path.parts:
                        raise ValueError(f"invalid workspace path: {item.workspace_path}")
                    cur.execute("select * from taskhub_pipelines where project=%s and name=%s", (slug, item.name))
                    existing_pipeline = cur.fetchone()
                    cur.execute("select * from taskhub_project_workspaces where project_id=%s and branch=%s", (project["id"], item.branch))
                    existing_workspace = cur.fetchone()
                    workspace_id = (
                        (existing_pipeline or {}).get("workspace_id")
                        or (existing_workspace or {}).get("workspace_id")
                        or item.workspace_id
                    )
                    workspace_path = (existing_workspace or {}).get("workspace_path") or str(path)
                    cur.execute(
                        """insert into taskhub_pipelines(id,project,name,state,workspace_id,default_worker_id,metadata,created_at,updated_at)
                        values(%s,%s,%s,'active',%s,%s,%s,%s,%s)
                        on conflict(project,name) do update set default_worker_id=excluded.default_worker_id,
                        updated_at=excluded.updated_at returning *""",
                        (uuid.uuid4(), slug, item.name, workspace_id, item.worker_id, Jsonb({"onboarded": True}), timestamp, timestamp),
                    )
                    pipeline = cur.fetchone()
                    cur.execute(
                        """insert into taskhub_project_workspaces
                        (id,project_id,pipeline_id,worker_id,workspace_id,workspace_path,branch,baseline_commit,state,created_at,updated_at)
                        values(%s,%s,%s,%s,%s,%s,%s,%s,'ready',%s,%s)
                        on conflict(project_id,branch) do update set pipeline_id=excluded.pipeline_id,worker_id=excluded.worker_id,
                        workspace_path=excluded.workspace_path,baseline_commit=excluded.baseline_commit,updated_at=excluded.updated_at""",
                        (uuid.uuid4(), project["id"], pipeline["id"], item.worker_id, workspace_id, workspace_path, item.branch,
                         preflight["snapshot"]["commit"], timestamp, timestamp),
                    )
                    steps.append({"key": f"pipeline:{item.workspace_id}", "label": item.name, "status": "ok", "detail": item.worker_id})
                cur.execute(
                    """insert into taskhub_source_sync(project,enabled,ssh_user,ssh_port,interval_seconds,status,last_snapshot_id,
                    last_synced_at,next_sync_at,updated_by,updated_at) values(%s,%s,%s,%s,%s,'ok',%s,%s,%s,%s,%s)
                    on conflict(project) do update set enabled=excluded.enabled,ssh_user=excluded.ssh_user,ssh_port=excluded.ssh_port,
                    interval_seconds=excluded.interval_seconds,status='ok',last_snapshot_id=excluded.last_snapshot_id,
                    last_synced_at=excluded.last_synced_at,next_sync_at=excluded.next_sync_at,updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
                    (slug, payload.sync_enabled, payload.source_ssh_user, payload.source_ssh_port, payload.sync_interval_seconds,
                     snapshot["id"], timestamp, timestamp, request.state.actor, timestamp),
                )
                cur.execute(
                    """insert into taskhub_project_budgets(project,monthly_budget_usd,warning_ratio,hard_limit,updated_by,updated_at)
                    values(%s,%s,0.8,false,%s,%s) on conflict(project) do update set monthly_budget_usd=excluded.monthly_budget_usd,
                    updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
                    (slug, payload.monthly_budget_usd, request.state.actor, timestamp),
                )
                cur.execute(
                    """insert into taskhub_governance_policies(project,autonomy_enabled,allowed_actions,max_risk,
                    require_human_release,require_human_publish,updated_by,updated_at)
                    values(%s,%s,%s,'low',true,true,%s,%s) on conflict(project) do update set
                    autonomy_enabled=excluded.autonomy_enabled,updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
                    (slug, payload.autonomy_enabled, sorted(AUTONOMY_ACTIONS), request.state.actor, timestamp),
                )
                steps.extend([
                    {"key": "sync", "label": "自动 Git 同步", "status": "ok", "detail": f"每 {payload.sync_interval_seconds} 秒"},
                    {"key": "governance", "label": "预算与治理", "status": "ok", "detail": "发布保持人工审批"},
                ])
                cur.execute(
                    """insert into taskhub_onboarding_runs(id,project,status,steps,actor,created_at,completed_at)
                    values(%s,%s,'completed',%s,%s,%s,%s)""",
                    (run_id, slug, Jsonb(steps), request.state.actor, timestamp, now_utc()),
                )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=409, detail=f"onboarding transaction failed: {exc}") from exc
    record_audit_event(request.state.actor, "integration.onboarding.completed", 200, detail={"project": slug, "run_id": str(run_id)})
    return {"id": run_id, "project": slug, "status": "completed", "steps": steps, "next": "launch_first_workflow"}


@router.post("/projects/{slug}/sync")
def sync_now(slug: str, request: Request) -> dict[str, Any]:
    result = sync_project_source(slug, request.state.actor)
    record_audit_event(request.state.actor, "integration.source.sync", 200, detail={"project": slug})
    return result


@router.get("/projects/{slug}/sync")
def sync_status(slug: str) -> dict[str, Any]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("select * from taskhub_source_sync where project=%s", (slug,))
            return cur.fetchone() or {"project": slug, "enabled": False, "status": "not_configured"}


@router.put("/projects/{slug}/sync")
def configure_sync(slug: str, request: Request, payload: SyncConfigRequest) -> dict[str, Any]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("select 1 from taskhub_projects where slug=%s", (slug,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="project not found")
            cur.execute(
                """insert into taskhub_source_sync(project,enabled,ssh_user,ssh_port,interval_seconds,status,
                next_sync_at,updated_by,updated_at) values(%s,%s,%s,%s,%s,'pending',%s,%s,%s)
                on conflict(project) do update set enabled=excluded.enabled,ssh_user=excluded.ssh_user,
                ssh_port=excluded.ssh_port,interval_seconds=excluded.interval_seconds,
                next_sync_at=excluded.next_sync_at,updated_by=excluded.updated_by,updated_at=excluded.updated_at returning *""",
                (slug, payload.enabled, payload.ssh_user, payload.ssh_port, payload.interval_seconds,
                 now_utc(), request.state.actor, now_utc()),
            )
            row = cur.fetchone()
        conn.commit()
    return row


@router.post("/projects/{slug}/first-workflow")
def launch_first_workflow(slug: str, payload: FirstWorkflowRequest) -> dict[str, Any]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("select 1 from taskhub_projects where slug=%s and state='active'", (slug,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="active project not found")
    return multi_invoke(PlannerRequest(
        requirement=payload.requirement, project=slug, title=payload.title,
        priority=payload.priority, pipeline_id=payload.pipeline_id,
        idempotency_key=f"first:{slug}:{hashlib.sha256(payload.requirement.encode()).hexdigest()[:24]}",
        metadata={"source": "one_click_onboarding", "guided_e2e": True},
    ))


@router.get("/devices")
def list_devices(refresh: bool = True) -> list[dict[str, Any]]:
    timestamp = now_utc()
    if refresh:
        with connect() as conn:
            with conn.cursor() as cur:
                for url in _worker_urls():
                    try:
                        health = _health(url)
                        worker_id = str(health.get("worker") or health.get("worker_id") or url)
                        task_types = [
                            item for item in str(health.get("task_types") or "").split(",")
                            if item and item not in {"market.price.collect", "commerce.listing.draft"}
                        ]
                        capabilities = redact(health)
                        for key in ("adb_ready", "adb_status", "adb_serial", "adb_model", "market_ready", "listing_ready"):
                            capabilities.pop(key, None)
                        cur.execute(
                            """insert into taskhub_devices(worker_id,url,platform,status,task_types,capabilities,last_seen_at,updated_at)
                            values(%s,%s,%s,'ok',%s,%s,%s,%s) on conflict(worker_id) do update set url=excluded.url,
                            platform=excluded.platform,status='ok',task_types=excluded.task_types,capabilities=excluded.capabilities,
                            last_seen_at=excluded.last_seen_at,updated_at=excluded.updated_at""",
                            (worker_id, url, str(health.get("platform") or health.get("hostname") or ""), task_types,
                             Jsonb(capabilities), timestamp, timestamp),
                        )
                    except Exception as exc:
                        cur.execute("update taskhub_devices set status='offline',capabilities=%s,updated_at=%s where url=%s", (Jsonb({"error": type(exc).__name__}), timestamp, url))
            conn.commit()
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("select * from taskhub_devices order by status,worker_id")
            return cur.fetchall()


@router.put("/artifacts/{artifact_id}/content")
async def upload_artifact(artifact_id: uuid.UUID, request: Request, project: str, name: str,
                          task_id: uuid.UUID | None = None, source_path: str | None = None) -> dict[str, Any]:
    maximum = int(os.getenv("TASKHUB_ARTIFACT_MAX_BYTES", str(20 * 1024 * 1024)))
    body = await request.body()
    if not body or len(body) > maximum:
        raise HTTPException(status_code=413, detail="artifact is empty or exceeds upload limit")
    safe_name = Path(name).name[:240]
    if not safe_name:
        raise HTTPException(status_code=400, detail="invalid artifact name")
    digest = hashlib.sha256(body).hexdigest()
    target_dir = ARTIFACT_ROOT / project / str(artifact_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / safe_name
    target.write_bytes(body)
    worker_id = str(getattr(request.state, "actor", "worker:unknown")).removeprefix("worker:")
    media_type = request.headers.get("content-type") or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """insert into taskhub_artifacts(id,project,task_id,worker_id,name,media_type,size_bytes,sha256,storage_path,source_path,metadata,created_at)
                values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict(project,sha256,name) do update set task_id=coalesce(excluded.task_id,taskhub_artifacts.task_id) returning *""",
                (artifact_id, project, task_id, worker_id, safe_name, media_type, len(body), digest, str(target), source_path, Jsonb({}), now_utc()),
            )
            row = cur.fetchone()
        conn.commit()
    return {key: row[key] for key in ("id", "project", "task_id", "worker_id", "name", "media_type", "size_bytes", "sha256", "created_at")}


@router.get("/artifacts")
def list_artifacts(project: str, task_id: uuid.UUID | None = None, limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
    where, params = "project=%s", [project]
    if task_id:
        where += " and task_id=%s"
        params.append(task_id)
    params.append(limit)
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"select id,project,task_id,worker_id,name,media_type,size_bytes,sha256,source_path,metadata,created_at from taskhub_artifacts where {where} order by created_at desc limit %s", params)
            return cur.fetchall()


@router.get("/artifacts/{artifact_id}/download")
def download_artifact(artifact_id: uuid.UUID) -> FileResponse:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("select * from taskhub_artifacts where id=%s", (artifact_id,))
            row = cur.fetchone()
    if not row or not Path(row["storage_path"]).is_file():
        raise HTTPException(status_code=404, detail="artifact not found")
    return FileResponse(row["storage_path"], media_type=row["media_type"], filename=row["name"])


@router.post("/governance/execute")
def execute_governed_action(request: Request, payload: GovernanceExecuteRequest) -> dict[str, Any]:
    if payload.action not in AUTONOMY_ACTIONS or payload.risk_level not in {"low", "medium", "high"}:
        raise HTTPException(status_code=400, detail="invalid governed action")
    execution_id, timestamp = uuid.uuid4(), now_utc()
    with connect() as conn:
        with conn.cursor() as cur:
            policy = governance_policy(cur, payload.project)
            decision, reason = decide_governance(policy, payload.action, payload.risk_level)
            cur.execute(
                """insert into taskhub_governance_executions(id,project,action,entity_id,decision,status,detail,actor,created_at)
                values(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (execution_id, payload.project, payload.action, payload.entity_id, decision,
                 "pending" if decision == "allowed" else "not_executed", Jsonb({"reason": reason}), request.state.actor, timestamp),
            )
        conn.commit()
    if decision != "allowed":
        return {"id": execution_id, "decision": decision, "status": "not_executed", "reason": reason}
    try:
        if payload.action == "refresh_context":
            result = sync_project_source(payload.project, request.state.actor)
        elif payload.action == "run_quality_evaluation":
            result = evaluate_workflow(payload.project, uuid.UUID(payload.entity_id), request.state.actor)
        elif payload.action == "retry_task":
            result = retry_task(uuid.UUID(payload.entity_id), request, TaskAction(reason=payload.reason))
        else:
            task_id = uuid.UUID(payload.entity_id)
            with connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """update taskhub_tasks set state='pending',worker_id=null,lease_token=null,lease_expires_at=null,updated_at=%s
                        where id=%s and state='running' and lease_expires_at<%s returning id,state""",
                        (now_utc(), task_id, now_utc()),
                    )
                    result = cur.fetchone()
                    if not result:
                        raise ValueError("task has no expired running lease")
                    cur.execute(
                        """update taskhub_resource_leases set task_id=null,pipeline_id=null,worker_id=null,
                        lease_token=null,expires_at=null,updated_at=%s where task_id=%s""",
                        (now_utc(), task_id),
                    )
                    cur.execute(
                        """insert into taskhub_task_history(id,task_id,from_state,to_state,actor,reason,payload,created_at)
                        values(%s,%s,'running','pending',%s,%s,%s,%s)""",
                        (uuid.uuid4(), task_id, request.state.actor, payload.reason,
                         Jsonb({"governance_execution_id": str(execution_id)}), now_utc()),
                    )
                conn.commit()
        status, detail = "executed", redact(result)
    except Exception as exc:
        status, detail = "failed", {"error": str(exc)[-1000:]}
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("update taskhub_governance_executions set status=%s,detail=%s,completed_at=%s where id=%s", (status, Jsonb(detail), now_utc(), execution_id))
        conn.commit()
    return {"id": execution_id, "decision": decision, "status": status, "detail": detail}


@router.get("/governance/executions")
def governance_executions(project: str, limit: int = Query(50, ge=1, le=200)) -> list[dict[str, Any]]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("select * from taskhub_governance_executions where project=%s order by created_at desc limit %s", (project, limit))
            return cur.fetchall()


def run_integration_cycle() -> bool:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("select project from taskhub_source_sync where enabled and coalesce(next_sync_at,now())<=now() order by next_sync_at nulls first limit 1")
            row = cur.fetchone()
    if not row:
        return False
    try:
        sync_project_source(row["project"])
    except Exception:
        pass
    return True
