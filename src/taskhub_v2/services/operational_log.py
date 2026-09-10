from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any

_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.S),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~-]{8,}"),
    re.compile(
        r"(?i)(authorization|token|password|secret|api[_-]?key)"
        r"(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;]+)"
    ),
    re.compile(r"\b(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{12,})\b"),
    re.compile(r"(?i)(://[^:/\s]+:)[^@\s]+(@)"),
)
_IP_PATTERN = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def redact_text(value: str, *, addresses: bool = False) -> str:
    result = value[:20_000]
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 3:
            result = pattern.sub(r"\1\2[REDACTED]", result)
        else:
            result = pattern.sub("[REDACTED]", result)
    if addresses:
        result = _IP_PATTERN.sub("[REDACTED-IP]", result)
    return result


def sanitize(value: Any, *, addresses: bool = False) -> Any:
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            if any(
                part in key.lower()
                for part in ("authorization", "password", "private_key", "token", "secret")
            ):
                clean[key] = "[REDACTED]"
            else:
                clean[key] = sanitize(item, addresses=addresses)
        return clean
    if isinstance(value, list):
        return [sanitize(item, addresses=addresses) for item in value]
    if isinstance(value, str):
        return redact_text(value, addresses=addresses)
    return value


class OperationalLog:
    def __init__(self, path: str, retention_days: int = 30):
        self.path = Path(path)
        self.retention_days = max(1, retention_days)
        self.lock = RLock()

    def record(self, operation: str, result: str, **context: Any) -> None:
        event = {
            "created_at": datetime.now(UTC).isoformat(),
            "operation": operation,
            "result": result,
            **sanitize(context),
        }
        with self.lock:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            os.chmod(self.path, 0o600)
            self._prune()

    def list(self, *, host_id: str = "", node_id: str = "", limit: int = 100) -> list[dict]:
        if not self.path.is_file():
            return []
        with self.lock:
            lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        events = []
        for line in reversed(lines):
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if host_id and event.get("host_id") != host_id:
                continue
            if node_id and event.get("node_id") != node_id:
                continue
            events.append(sanitize(event))
            if len(events) >= max(1, min(limit, 500)):
                break
        return events

    def _prune(self) -> None:
        if not self.path.is_file() or self.path.stat().st_size < 2_000_000:
            return
        cutoff = datetime.now(UTC) - timedelta(days=self.retention_days)
        retained = []
        for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                created = datetime.fromisoformat(json.loads(line)["created_at"])
            except (ValueError, KeyError, TypeError):
                continue
            if created >= cutoff:
                retained.append(line)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text("\n".join(retained) + ("\n" if retained else ""), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)
