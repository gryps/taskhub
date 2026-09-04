from __future__ import annotations

import os
import logging
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from app.monitoring import collect_database_alerts
from app.planner import env_file_status, read_env_file, schedule_control_restart, write_env_file
from app.taskhub import connect, now_utc, redact


router = APIRouter(prefix="/taskhub/operations/notifications", tags=["operations"])
logger = logging.getLogger(__name__)

CHANNELS = {"generic", "wecom", "dingtalk"}
SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}


class NotificationConfigRequest(BaseModel):
    enabled: bool = False
    channel: Literal["generic", "wecom", "dingtalk"] = "generic"
    webhook_url: str | None = None
    minimum_severity: Literal["critical", "warning", "info"] = "critical"
    poll_seconds: int = Field(30, ge=10, le=3600)
    retry_seconds: int = Field(120, ge=10, le=86400)
    max_attempts: int = Field(3, ge=1, le=20)
    restart: bool = False


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return min(maximum, max(minimum, int(os.getenv(name, str(default)))))
    except ValueError:
        return default


def mask_webhook_url(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "********"
    path_parts = parsed.path.rstrip("/").split("/")
    if path_parts and path_parts[-1]:
        path_parts[-1] = "********"
    return urlunsplit((parsed.scheme, parsed.netloc, "/".join(path_parts), "", ""))


def notification_config() -> dict[str, Any]:
    channel = os.getenv("TASKHUB_NOTIFICATION_CHANNEL", "generic").lower()
    if channel not in CHANNELS:
        channel = "generic"
    severity = os.getenv("TASKHUB_NOTIFICATION_MIN_SEVERITY", "critical").lower()
    if severity not in SEVERITY_RANK:
        severity = "critical"
    webhook_url = os.getenv("TASKHUB_NOTIFICATION_WEBHOOK_URL", "").strip()
    enabled = env_bool("TASKHUB_NOTIFICATION_ENABLED")
    return {
        "enabled": enabled,
        "ready": enabled and bool(webhook_url),
        "channel": channel,
        "webhook_url_mask": mask_webhook_url(webhook_url),
        "webhook_configured": bool(webhook_url),
        "minimum_severity": severity,
        "poll_seconds": env_int("TASKHUB_NOTIFICATION_POLL_SECONDS", 30, 10, 3600),
        "retry_seconds": env_int("TASKHUB_NOTIFICATION_RETRY_SECONDS", 120, 10, 86400),
        "max_attempts": env_int("TASKHUB_NOTIFICATION_MAX_ATTEMPTS", 3, 1, 20),
        "direct_connection": True,
    }


def validate_webhook_url(value: str) -> str:
    url = value.strip()
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="webhook URL must use http or https")
    return url


def alert_text(alert: dict[str, Any], test: bool = False) -> str:
    prefix = "[TaskHub 测试]" if test else f"[TaskHub {alert['severity'].upper()}]"
    lines = [prefix, alert["title"], alert["message"]]
    if not test:
        lines.append(f"{alert['code']} | {alert['entity_type']} {alert['entity_id']}")
    public_url = os.getenv("TASKHUB_PUBLIC_URL", "").rstrip("/")
    if public_url:
        lines.append(public_url)
    return "\n\n".join(lines)


def webhook_payload(channel: str, alert: dict[str, Any], test: bool = False) -> dict[str, Any]:
    content = alert_text(alert, test)
    if channel == "wecom":
        return {"msgtype": "markdown", "markdown": {"content": content}}
    if channel == "dingtalk":
        return {"msgtype": "markdown", "markdown": {"title": alert["title"], "text": content}}
    return {
        "source": "gryps-taskhub",
        "test": test,
        "alert": redact({**alert, "text": content}),
    }


def send_webhook(channel: str, webhook_url: str, alert: dict[str, Any], test: bool = False) -> int:
    with httpx.Client(timeout=10, trust_env=False) as client:
        response = client.post(webhook_url, json=webhook_payload(channel, alert, test))
        response.raise_for_status()
        body = response.json() if channel in {"wecom", "dingtalk"} and response.content else {}
    if channel == "wecom" and body.get("errcode") not in {None, 0}:
        raise RuntimeError(f"wecom errcode {body.get('errcode')}")
    if channel == "dingtalk" and body.get("errcode") not in {None, 0}:
        raise RuntimeError(f"dingtalk errcode {body.get('errcode')}")
    return response.status_code


def queue_active_alerts(config: dict[str, Any]) -> int:
    threshold = SEVERITY_RANK[config["minimum_severity"]]
    alerts = [
        alert for alert in collect_database_alerts()
        if not alert["acknowledged"] and SEVERITY_RANK[alert["severity"]] <= threshold
    ]
    queued = 0
    timestamp = now_utc()
    with connect() as conn:
        with conn.cursor() as cur:
            for alert in alerts:
                cur.execute(
                    """
                    insert into taskhub_alert_deliveries
                        (id, fingerprint, channel, severity, alert, status, attempt_count,
                         created_at, updated_at, next_retry_at)
                    values (%s, %s, %s, %s, %s, 'pending', 0, %s, %s, %s)
                    on conflict (fingerprint, channel) do nothing
                    """,
                    (uuid.uuid4(), alert["fingerprint"], config["channel"], alert["severity"],
                     Jsonb(redact(jsonable_encoder(alert))), timestamp, timestamp, timestamp),
                )
                queued += cur.rowcount
        conn.commit()
    return queued


def deliver_due_alerts(config: dict[str, Any]) -> dict[str, int]:
    timestamp = now_utc()
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select delivery.*
                from taskhub_alert_deliveries delivery
                left join taskhub_alert_acknowledgements ack
                  on ack.fingerprint = delivery.fingerprint
                where delivery.channel = %s
                  and delivery.status in ('pending', 'failed')
                  and delivery.attempt_count < %s
                  and delivery.next_retry_at <= %s
                  and ack.fingerprint is null
                order by delivery.created_at
                limit 20
                """,
                (config["channel"], config["max_attempts"], timestamp),
            )
            rows = cur.fetchall()

    delivered = failed = 0
    for row in rows:
        attempt_time = now_utc()
        try:
            response_status = send_webhook(config["channel"], os.environ["TASKHUB_NOTIFICATION_WEBHOOK_URL"], row["alert"])
            status = "delivered"
            error = None
            delivered_at = attempt_time
            delivered += 1
        except Exception as exc:
            response_status = getattr(getattr(exc, "response", None), "status_code", None)
            status = "failed"
            error = f"{exc.__class__.__name__}: {exc}"[:1000]
            delivered_at = None
            failed += 1
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    update taskhub_alert_deliveries
                    set status = %s, attempt_count = attempt_count + 1, last_error = %s,
                        response_status = %s, attempted_at = %s, delivered_at = %s,
                        next_retry_at = %s, updated_at = %s
                    where id = %s
                    """,
                    (status, error, response_status, attempt_time, delivered_at,
                     attempt_time + timedelta(seconds=config["retry_seconds"]), attempt_time, row["id"]),
                )
            conn.commit()
    return {"selected": len(rows), "delivered": delivered, "failed": failed}


def run_notification_cycle() -> dict[str, Any]:
    config = notification_config()
    if not config["ready"]:
        return {"enabled": config["enabled"], "ready": False, "queued": 0, "selected": 0, "delivered": 0, "failed": 0}
    queued = queue_active_alerts(config)
    return {"enabled": True, "ready": True, "queued": queued, **deliver_due_alerts(config)}


def run_notification_cycle_safely() -> dict[str, Any]:
    try:
        return run_notification_cycle()
    except Exception as exc:
        logger.exception("TaskHub notification cycle failed")
        return {"enabled": True, "ready": True, "error": exc.__class__.__name__}


@router.get("/config")
def get_notification_config() -> dict[str, Any]:
    return {"ok": True, "config": notification_config(), "env_file": env_file_status()}


@router.post("/config")
def save_notification_config(request: NotificationConfigRequest) -> dict[str, Any]:
    env_path = Path(os.getenv("LANGGRAPH_ENV_FILE", "/home/gryps/apps/langgraph-control/.env"))
    values = read_env_file(env_path)
    existing_url = values.get("TASKHUB_NOTIFICATION_WEBHOOK_URL", "")
    submitted_url = (request.webhook_url or "").strip()
    webhook_url = existing_url if not submitted_url or "********" in submitted_url else validate_webhook_url(submitted_url)
    if request.enabled and not webhook_url:
        raise HTTPException(status_code=400, detail="启用通知时必须填写 Webhook 地址")
    updates = {
        "TASKHUB_NOTIFICATION_ENABLED": str(request.enabled).lower(),
        "TASKHUB_NOTIFICATION_CHANNEL": request.channel,
        "TASKHUB_NOTIFICATION_MIN_SEVERITY": request.minimum_severity,
        "TASKHUB_NOTIFICATION_POLL_SECONDS": str(request.poll_seconds),
        "TASKHUB_NOTIFICATION_RETRY_SECONDS": str(request.retry_seconds),
        "TASKHUB_NOTIFICATION_MAX_ATTEMPTS": str(request.max_attempts),
    }
    if webhook_url:
        updates["TASKHUB_NOTIFICATION_WEBHOOK_URL"] = webhook_url
    else:
        values.pop("TASKHUB_NOTIFICATION_WEBHOOK_URL", None)
    values.update(updates)
    for key, value in updates.items():
        os.environ[key] = value
    if webhook_url:
        os.environ["TASKHUB_NOTIFICATION_WEBHOOK_URL"] = webhook_url
    else:
        os.environ.pop("TASKHUB_NOTIFICATION_WEBHOOK_URL", None)
    backup_path = write_env_file(env_path, values)
    restart = schedule_control_restart() if request.restart else {"scheduled": False}
    return {
        "ok": True, "config": notification_config(), "backup_path": backup_path,
        "restart": restart, "message": "通知配置已保存。" + (" 控制服务正在重启。" if request.restart else ""),
    }


@router.post("/test")
def test_notification() -> dict[str, Any]:
    config = notification_config()
    if not config["ready"]:
        raise HTTPException(status_code=400, detail="通知通道未启用或 Webhook 未配置")
    alert = {
        "fingerprint": "test", "code": "NOTIFICATION_TEST", "severity": "info",
        "category": "system", "title": "TaskHub 通知测试", "message": "控制中心通知通道连接正常。",
        "entity_type": "system", "entity_id": "controller", "detected_at": now_utc().isoformat(), "detail": {},
    }
    status = send_webhook(config["channel"], os.environ["TASKHUB_NOTIFICATION_WEBHOOK_URL"], alert, test=True)
    return {"ok": True, "channel": config["channel"], "response_status": status}


@router.get("/deliveries")
def list_notification_deliveries(limit: int = 20) -> dict[str, Any]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, fingerprint, channel, severity, status, attempt_count, last_error,
                       response_status, attempted_at, delivered_at, created_at
                from taskhub_alert_deliveries order by created_at desc limit %s
                """,
                (min(max(limit, 1), 100),),
            )
            rows = cur.fetchall()
    return {"deliveries": rows}
