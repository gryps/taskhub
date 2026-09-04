from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.provider_health import provider_health
from app.taskhub import connect, now_utc, redact


router = APIRouter(prefix="/taskhub/operations", tags=["operations"])

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


class AlertAcknowledgement(BaseModel):
    fingerprint: str = Field(..., min_length=64, max_length=64)


def env_seconds(name: str, default: int, minimum: int = 60) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def env_count(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def alert_fingerprint(code: str, entity_type: str, entity_id: Any, occurrence: Any = "") -> str:
    value = json.dumps(
        [code, entity_type, str(entity_id), str(occurrence or "")],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(value.encode()).hexdigest()


def make_alert(
    *,
    code: str,
    severity: str,
    category: str,
    title: str,
    message: str,
    entity_type: str,
    entity_id: Any,
    detected_at: datetime | None,
    occurrence: Any = "",
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "fingerprint": alert_fingerprint(code, entity_type, entity_id, occurrence),
        "code": code,
        "severity": severity,
        "category": category,
        "title": title,
        "message": message,
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "detected_at": detected_at,
        "detail": redact(detail or {}),
    }


def collect_database_alerts(timestamp: datetime | None = None) -> list[dict[str, Any]]:
    now = timestamp or now_utc()
    pending_before = now - timedelta(seconds=env_seconds("TASKHUB_ALERT_PENDING_SECONDS", 1800))
    workflow_before = now - timedelta(seconds=env_seconds("WORKFLOW_ALERT_STALE_SECONDS", 1800))
    role_before = now - timedelta(seconds=env_seconds("WORKFLOW_ROLE_STALE_SECONDS", 900, 120))
    role_history_after = now - timedelta(seconds=env_seconds("WORKFLOW_ALERT_HISTORY_SECONDS", 86400, 3600))
    slow_role_seconds = env_seconds("WORKFLOW_ROLE_SLOW_SECONDS", 120, 30)
    max_retries = env_count("TASKHUB_ALERT_MAX_RETRIES", 3)
    role_max_attempts = env_count("WORKFLOW_ROLE_MAX_ATTEMPTS", 3)
    alerts: list[dict[str, Any]] = []

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, title, state, worker_id, target_worker_id, retry_count,
                       created_at, updated_at, claimed_at, heartbeat_at, lease_expires_at
                from taskhub_tasks
                where state in ('running', 'pending', 'failed', 'blocked')
                order by updated_at
                limit 500
                """
            )
            tasks = cur.fetchall()
            cur.execute(
                """
                select id, project, state, summary, active_role, updated_at
                from taskhub_workflows
                where state not in ('released', 'canceled') and updated_at < %s
                order by updated_at
                limit 200
                """,
                (workflow_before,),
            )
            workflows = cur.fetchall()
            cur.execute(
                """
                select id, workflow_id, state, role, status, attempt_count, provider,
                       model, output, error, started_at, completed_at, next_retry_at
                from taskhub_workflow_role_runs
                where status in ('running', 'failed')
                   or (status = 'succeeded' and completed_at >= %s)
                order by case when status in ('running', 'failed') then 0 else 1 end, started_at desc
                limit 200
                """,
                (role_history_after,),
            )
            role_runs = cur.fetchall()
            cur.execute("select fingerprint, acknowledged_by, acknowledged_at from taskhub_alert_acknowledgements")
            acknowledgements = {row["fingerprint"]: row for row in cur.fetchall()}

    for task in tasks:
        occurrence = task.get("claimed_at") or task["updated_at"]
        if task["state"] == "running" and (
            (task.get("lease_expires_at") and task["lease_expires_at"] < now)
            or (task.get("heartbeat_at") and task["heartbeat_at"] < now - timedelta(seconds=env_seconds("TASKHUB_LEASE_SECONDS", 120)))
        ):
            alerts.append(make_alert(
                code="TASK_SIGNAL_LOST", severity="critical", category="task",
                title="实施任务失去心跳",
                message=f"{task['title']} 的实施节点 {task.get('worker_id') or '未知'} 已超过租约/心跳期限。",
                entity_type="task", entity_id=task["id"], detected_at=task.get("heartbeat_at") or task["updated_at"],
                occurrence=occurrence, detail={"worker_id": task.get("worker_id"), "lease_expires_at": task.get("lease_expires_at")},
            ))
        elif task["state"] == "pending" and task["updated_at"] < pending_before:
            alerts.append(make_alert(
                code="TASK_PENDING_TOO_LONG", severity="warning", category="task",
                title="任务等待时间过长", message=f"{task['title']} 长时间未被目标节点领取。",
                entity_type="task", entity_id=task["id"], detected_at=task["updated_at"], occurrence=task["updated_at"],
                detail={"target_worker_id": task.get("target_worker_id"), "retry_count": task["retry_count"]},
            ))
        if task["state"] in {"failed", "blocked"}:
            alerts.append(make_alert(
                code=f"TASK_{task['state'].upper()}", severity="critical" if task["state"] == "blocked" else "warning",
                category="task", title="任务已阻塞" if task["state"] == "blocked" else "任务执行失败",
                message=task["title"], entity_type="task", entity_id=task["id"], detected_at=task["updated_at"],
                occurrence=task["updated_at"], detail={"retry_count": task["retry_count"], "worker_id": task.get("worker_id")},
            ))
        if task["retry_count"] >= max_retries:
            alerts.append(make_alert(
                code="TASK_RETRY_LIMIT", severity="critical", category="task", title="任务重试次数过高",
                message=f"{task['title']} 已重试 {task['retry_count']} 次。", entity_type="task", entity_id=task["id"],
                detected_at=task["updated_at"], occurrence=f"{task['updated_at']}:{task['retry_count']}",
                detail={"retry_count": task["retry_count"], "threshold": max_retries},
            ))

    for workflow in workflows:
        alerts.append(make_alert(
            code="WORKFLOW_STALE", severity="warning", category="workflow", title="工作流长时间无进展",
            message=f"{workflow['summary']} 停留在 {workflow['state']} 阶段。", entity_type="workflow",
            entity_id=workflow["id"], detected_at=workflow["updated_at"],
            occurrence=f"{workflow['state']}:{workflow['updated_at']}", detail={"state": workflow["state"], "active_role": workflow.get("active_role")},
        ))

    for run in role_runs:
        if run["status"] == "running" and run["started_at"] < role_before:
            alerts.append(make_alert(
                code="ROLE_RUN_STALE", severity="critical", category="model", title="角色模型调用超时",
                message=f"{run['role']} 在 {run['state']} 阶段长时间未返回。", entity_type="workflow",
                entity_id=run["workflow_id"], detected_at=run["started_at"], occurrence=run["id"],
                detail={"run_id": run["id"], "role": run["role"], "attempt_count": run["attempt_count"]},
            ))
        elif run["status"] == "failed":
            exhausted = run["attempt_count"] >= role_max_attempts or not run.get("next_retry_at")
            error = run.get("error") or {}
            reason = str(error.get("reason") or error.get("message") or "模型调用失败")
            alerts.append(make_alert(
                code="ROLE_RETRY_EXHAUSTED" if exhausted else "ROLE_RETRY_PENDING",
                severity="critical" if exhausted else "warning", category="model",
                title="角色重试已耗尽" if exhausted else "角色调用等待重试",
                message=f"{run['role']}：{reason}", entity_type="workflow", entity_id=run["workflow_id"],
                detected_at=run.get("completed_at") or run["started_at"], occurrence=run["id"],
                detail={"run_id": run["id"], "attempt_count": run["attempt_count"], "next_retry_at": run.get("next_retry_at"), "error": error},
            ))
        elif run["status"] == "succeeded":
            output = run.get("output") or {}
            attempts = output.get("attempts") if isinstance(output, dict) else []
            attempts = attempts if isinstance(attempts, list) else []
            fallback_attempts = [item for item in attempts[:-1] if isinstance(item, dict)]
            if fallback_attempts:
                path = " > ".join(str(item.get("provider") or "unknown") for item in attempts if isinstance(item, dict))
                alerts.append(make_alert(
                    code="ROLE_FALLBACK_USED", severity="warning", category="model", title="角色调用使用了 fallback",
                    message=f"{run['role']} 调用路径：{path or run.get('provider') or 'unknown'}。",
                    entity_type="workflow", entity_id=run["workflow_id"], detected_at=run.get("completed_at"), occurrence=run["id"],
                    detail={"run_id": run["id"], "provider": run.get("provider"), "attempts": attempts},
                ))
                quota_attempts = [item for item in fallback_attempts if item.get("reason") == "quota_exceeded"]
                if quota_attempts:
                    providers = ", ".join(str(item.get("provider") or "unknown") for item in quota_attempts)
                    alerts.append(make_alert(
                        code="PROVIDER_QUOTA_EVENT", severity="critical", category="provider", title="模型额度触发 fallback",
                        message=f"{providers} 额度已用尽，本次角色调用已切换后续 Provider。",
                        entity_type="workflow", entity_id=run["workflow_id"], detected_at=run.get("completed_at"), occurrence=run["id"],
                        detail={"run_id": run["id"], "providers": providers},
                    ))
            duration = (run["completed_at"] - run["started_at"]).total_seconds() if run.get("completed_at") else 0
            if duration >= slow_role_seconds:
                alerts.append(make_alert(
                    code="ROLE_RUN_SLOW", severity="warning", category="model", title="角色模型调用耗时过长",
                    message=f"{run['role']} 本次调用耗时 {int(duration)} 秒。", entity_type="workflow",
                    entity_id=run["workflow_id"], detected_at=run.get("completed_at"), occurrence=run["id"],
                    detail={"run_id": run["id"], "duration_seconds": duration, "provider": run.get("provider"), "model": run.get("model")},
                ))

    for item in provider_health():
        if item["status"] not in {"degraded", "cooldown", "probing"}:
            continue
        severity = "critical" if item.get("reason") == "quota_exceeded" else "warning"
        alerts.append(make_alert(
            code="PROVIDER_QUOTA_EXHAUSTED" if severity == "critical" else "PROVIDER_DEGRADED",
            severity=severity, category="provider", title="模型额度已用尽" if severity == "critical" else "Provider 已降级",
            message=f"{item['provider']}：{item.get('reason') or item['status']}，系统正在使用 fallback。",
            entity_type="provider", entity_id=item["provider"], detected_at=now,
            occurrence=item.get("failed_at") or item.get("reason"), detail=item,
        ))

    for item in alerts:
        acknowledgement = acknowledgements.get(item["fingerprint"])
        item["acknowledged"] = bool(acknowledgement)
        item["acknowledged_by"] = acknowledgement.get("acknowledged_by") if acknowledgement else None
        item["acknowledged_at"] = acknowledgement.get("acknowledged_at") if acknowledgement else None
    alerts.sort(key=lambda item: (item["acknowledged"], SEVERITY_ORDER[item["severity"]], item["detected_at"]))
    return alerts


@router.get("/alerts")
def list_operational_alerts() -> dict[str, Any]:
    alerts = collect_database_alerts()
    active = [item for item in alerts if not item["acknowledged"]]
    return {
        "summary": {
            "critical": sum(item["severity"] == "critical" for item in active),
            "warning": sum(item["severity"] == "warning" for item in active),
            "info": sum(item["severity"] == "info" for item in active),
            "acknowledged": len(alerts) - len(active),
        },
        "thresholds": {
            "pending_seconds": env_seconds("TASKHUB_ALERT_PENDING_SECONDS", 1800),
            "workflow_stale_seconds": env_seconds("WORKFLOW_ALERT_STALE_SECONDS", 1800),
            "role_stale_seconds": env_seconds("WORKFLOW_ROLE_STALE_SECONDS", 900, 120),
            "role_slow_seconds": env_seconds("WORKFLOW_ROLE_SLOW_SECONDS", 120, 30),
        },
        "alerts": alerts,
    }


@router.post("/alerts/acknowledge")
def acknowledge_operational_alert(payload: AlertAcknowledgement, request: Request) -> dict[str, Any]:
    alerts = collect_database_alerts()
    if payload.fingerprint not in {item["fingerprint"] for item in alerts}:
        raise HTTPException(status_code=404, detail="active alert not found")
    timestamp = now_utc()
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into taskhub_alert_acknowledgements (fingerprint, acknowledged_by, acknowledged_at)
                values (%s, %s, %s)
                on conflict (fingerprint) do update
                set acknowledged_by = excluded.acknowledged_by, acknowledged_at = excluded.acknowledged_at
                """,
                (payload.fingerprint, request.state.actor, timestamp),
            )
        conn.commit()
    return {"fingerprint": payload.fingerprint, "acknowledged": True, "acknowledged_at": timestamp}
