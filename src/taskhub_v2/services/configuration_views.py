import re
from datetime import datetime
from typing import Any


def metadata(record) -> dict[str, Any]:
    if not record:
        return {
            "version": 0,
            "applied_version": 0,
            "restart_required": False,
            "updated_at": None,
            "applied_at": None,
        }
    return {
        "version": record.version,
        "applied_version": record.applied_version,
        "restart_required": record.version != record.applied_version,
        "updated_at": record.updated_at.isoformat(),
        "applied_at": record.applied_at.isoformat() if record.applied_at else None,
    }


def audit_view(item: dict[str, Any]) -> dict[str, Any]:
    created = item["created_at"]
    return {
        **item,
        "created_at": created.isoformat() if isinstance(created, datetime) else str(created),
    }


def safe_error(error: Exception) -> str:
    text = re.sub(r"(?i)bearer\s+\S+", "Bearer [redacted]", str(error))
    text = re.sub(r"[A-Za-z0-9_-]{24,}", "[redacted]", text)
    return f"连接失败：{type(error).__name__} · {text[:180]}"
