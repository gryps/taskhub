from __future__ import annotations

import hashlib
import logging
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from app.monitoring import collect_database_alerts
from app.planner import env_file_status, read_env_file, schedule_control_restart, write_env_file
from app.taskhub import add_history, connect, now_utc, redact, release_resource_leases


router = APIRouter(prefix="/taskhub/operations/policy", tags=["operations"])
logger = logging.getLogger(__name__)


class OperationsPolicyRequest(BaseModel):
    enabled: bool = False
    mode: Literal["dry_run", "active"] = "dry_run"
    poll_seconds: int = Field(30, ge=10, le=3600)
    warning_escalation_seconds: int = Field(1800, ge=60, le=604800)
    critical_escalation_seconds: int = Field(3600, ge=120, le=604800)
    cooldown_seconds: int = Field(900, ge=60, le=86400)
    max_task_retries: int = Field(3, ge=1, le=20)
    restart: bool = False


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    return default if value is None else value.lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return min(maximum, max(minimum, int(os.getenv(name, str(default)))))
    except ValueError:
        return default


def operations_policy() -> dict[str, Any]:
    mode = os.getenv("TASKHUB_REMEDIATION_MODE", "dry_run").lower()
    if mode not in {"dry_run", "active"}:
        mode = "dry_run"
    warning = env_int("TASKHUB_ESCALATE_WARNING_SECONDS", 1800, 60, 604800)
    critical = env_int("TASKHUB_ESCALATE_CRITICAL_SECONDS", 3600, 120, 604800)
    return {
        "enabled": env_bool("TASKHUB_REMEDIATION_ENABLED"),
        "mode": mode,
        "poll_seconds": env_int("TASKHUB_REMEDIATION_POLL_SECONDS", 30, 10, 3600),
        "warning_escalation_seconds": warning,
        "critical_escalation_seconds": max(warning, critical),
        "cooldown_seconds": env_int("TASKHUB_REMEDIATION_COOLDOWN_SECONDS", 900, 60, 86400),
        "max_task_retries": env_int("TASKHUB_REMEDIATION_MAX_TASK_RETRIES", 3, 1, 20),
        "safe_actions": ["requeue_expired_lease", "retry_opted_in_task", "retry_opted_in_role"],
    }


def _age_seconds(alert: dict[str, Any], now: datetime) -> float:
    detected = alert.get("detected_at")
    if isinstance(detected, str):
        try:
            detected = datetime.fromisoformat(detected.replace("Z", "+00:00"))
        except ValueError:
            return 0
    return max(0, (now - detected).total_seconds()) if isinstance(detected, datetime) else 0


def escalate_alerts(
    alerts: list[dict[str, Any]], policy: dict[str, Any] | None = None, timestamp: datetime | None = None
) -> list[dict[str, Any]]:
    config = policy or operations_policy()
    now = timestamp or now_utc()
    output = []
    for source in alerts:
        alert = {**source}
        age = _age_seconds(alert, now)
        level = 0
        if not alert.get("acknowledged") and age >= config["critical_escalation_seconds"]:
            level = 2
        elif not alert.get("acknowledged") and age >= config["warning_escalation_seconds"]:
            level = 1
        alert["source_fingerprint"] = source["fingerprint"]
        alert["escalation_level"] = level
        alert["age_seconds"] = int(age)
        if level:
            alert["severity"] = "critical"
            alert["title"] = f"[升级 L{level}] {alert['title']}"
            alert["fingerprint"] = hashlib.sha256(f"{source['fingerprint']}:{level}".encode()).hexdigest()
        output.append(alert)
    return output


def _candidate_action(alert: dict[str, Any]) -> str | None:
    return {
        "TASK_SIGNAL_LOST": "requeue_expired_lease",
        "TASK_FAILED": "retry_opted_in_task",
        "ROLE_RETRY_EXHAUSTED": "retry_opted_in_role",
    }.get(str(alert.get("code")))


def _execute_action(cur, alert: dict[str, Any], action: str, policy: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    entity_id = alert["entity_id"]
    timestamp = now_utc()
    if action == "requeue_expired_lease":
        cur.execute("select * from taskhub_tasks where id = %s for update", (entity_id,))
        task = cur.fetchone()
        if not task or task["state"] != "running" or not task.get("lease_expires_at") or task["lease_expires_at"] >= timestamp:
            return "skipped", {"reason": "lease is no longer expired"}
        release_resource_leases(cur, task["id"])
        cur.execute(
            """
            update taskhub_tasks set state = 'pending', worker_id = null, lease_token = null,
                lease_expires_at = null, heartbeat_at = null, claimed_at = null,
                retry_count = retry_count + 1, updated_at = %s where id = %s
            """,
            (timestamp, task["id"]),
        )
        add_history(cur, task["id"], "running", "pending", "taskhub:auto-recovery", "expired lease recovered")
        return "executed", {"task_id": str(task["id"]), "previous_worker": task.get("worker_id")}
    if action == "retry_opted_in_task":
        cur.execute("select * from taskhub_tasks where id = %s for update", (entity_id,))
        task = cur.fetchone()
        if not task or task["state"] != "failed":
            return "skipped", {"reason": "task is no longer failed"}
        if not (task.get("metadata") or {}).get("auto_retry"):
            return "skipped", {"reason": "task did not opt in to auto_retry"}
        if task["retry_count"] >= policy["max_task_retries"]:
            return "skipped", {"reason": "task retry limit reached"}
        cur.execute(
            """
            update taskhub_tasks set state = 'pending', worker_id = null, error = null,
                lease_token = null, lease_expires_at = null, heartbeat_at = null, claimed_at = null,
                retry_count = retry_count + 1, updated_at = %s where id = %s
            """,
            (timestamp, task["id"]),
        )
        add_history(cur, task["id"], "failed", "pending", "taskhub:auto-recovery", "opted-in task retried")
        return "executed", {"task_id": str(task["id"]), "retry_count": task["retry_count"] + 1}
    if action == "retry_opted_in_role":
        run_id = (alert.get("detail") or {}).get("run_id")
        cur.execute(
            """
            select run.*, workflow.context from taskhub_workflow_role_runs run
            join taskhub_workflows workflow on workflow.id = run.workflow_id
            where run.id = %s for update of run
            """,
            (run_id,),
        )
        run = cur.fetchone()
        if not run or run["status"] != "failed":
            return "skipped", {"reason": "role run is no longer failed"}
        if not (run.get("context") or {}).get("auto_retry_roles"):
            return "skipped", {"reason": "workflow did not opt in to auto_retry_roles"}
        cur.execute(
            "update taskhub_workflow_role_runs set attempt_count = 0, next_retry_at = %s where id = %s",
            (timestamp, run["id"]),
        )
        return "executed", {"run_id": str(run["id"]), "workflow_id": str(run["workflow_id"])}
    return "skipped", {"reason": "action is not allowlisted"}


def run_operations_cycle() -> dict[str, Any]:
    policy = operations_policy()
    alerts = escalate_alerts(collect_database_alerts(), policy)
    candidates = [(alert, _candidate_action(alert)) for alert in alerts if not alert.get("acknowledged")]
    candidates = [(alert, action) for alert, action in candidates if action]
    counts = {"proposed": 0, "executed": 0, "skipped": 0, "failed": 0}
    for alert, action in candidates:
        timestamp = now_utc()
        try:
            with connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        select * from taskhub_remediation_actions
                        where fingerprint = %s and action = %s and created_at >= %s
                        order by created_at desc limit 1
                        """,
                        (alert["source_fingerprint"], action, timestamp - timedelta(seconds=policy["cooldown_seconds"])),
                    )
                    if cur.fetchone():
                        continue
                    status, detail = ("proposed", {"reason": "dry-run policy"})
                    if policy["enabled"] and policy["mode"] == "active":
                        status, detail = _execute_action(cur, alert, action, policy)
                    cur.execute(
                        """
                        insert into taskhub_remediation_actions
                            (id, fingerprint, alert_code, entity_type, entity_id, action, mode, status, detail, created_at, completed_at)
                        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (uuid.uuid4(), alert["source_fingerprint"], alert["code"], alert["entity_type"], alert["entity_id"],
                         action, policy["mode"], status, Jsonb(redact(jsonable_encoder(detail))), timestamp, timestamp),
                    )
                conn.commit()
            counts[status] += 1
        except Exception as exc:
            logger.exception("TaskHub remediation action failed")
            counts["failed"] += 1
    return {"enabled": policy["enabled"], "mode": policy["mode"], "candidates": len(candidates), **counts}


def run_operations_cycle_safely() -> dict[str, Any]:
    try:
        return run_operations_cycle()
    except Exception as exc:
        logger.exception("TaskHub operations cycle failed")
        return {"error": exc.__class__.__name__}


@router.get("/config")
def get_operations_policy() -> dict[str, Any]:
    return {"ok": True, "policy": operations_policy(), "env_file": env_file_status()}


@router.post("/config")
def save_operations_policy(request: OperationsPolicyRequest) -> dict[str, Any]:
    env_path = Path(os.getenv("LANGGRAPH_ENV_FILE", "/home/gryps/apps/langgraph-control/.env"))
    values = read_env_file(env_path)
    updates = {
        "TASKHUB_REMEDIATION_ENABLED": str(request.enabled).lower(),
        "TASKHUB_REMEDIATION_MODE": request.mode,
        "TASKHUB_REMEDIATION_POLL_SECONDS": str(request.poll_seconds),
        "TASKHUB_ESCALATE_WARNING_SECONDS": str(request.warning_escalation_seconds),
        "TASKHUB_ESCALATE_CRITICAL_SECONDS": str(max(request.warning_escalation_seconds, request.critical_escalation_seconds)),
        "TASKHUB_REMEDIATION_COOLDOWN_SECONDS": str(request.cooldown_seconds),
        "TASKHUB_REMEDIATION_MAX_TASK_RETRIES": str(request.max_task_retries),
    }
    values.update(updates)
    os.environ.update(updates)
    backup_path = write_env_file(env_path, values)
    restart = schedule_control_restart() if request.restart else {"scheduled": False}
    return {"ok": True, "policy": operations_policy(), "backup_path": backup_path, "restart": restart}


@router.post("/run")
def run_operations_now() -> dict[str, Any]:
    return run_operations_cycle()


@router.get("/actions")
def list_remediation_actions(limit: int = 50) -> dict[str, Any]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select * from taskhub_remediation_actions order by created_at desc limit %s",
                (min(max(limit, 1), 200),),
            )
            rows = cur.fetchall()
    return {"actions": rows}
