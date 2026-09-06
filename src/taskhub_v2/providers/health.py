import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable


class ProviderHealthStore:
    """Small operational state store; workflow truth remains in LangGraph checkpoints."""

    def __init__(
        self,
        path: str,
        quota_cooldown_seconds: int = 3600,
        transient_cooldown_seconds: int = 60,
        clock: Callable[[], float] = time.time,
    ):
        self.path = Path(path)
        self.quota_cooldown_seconds = quota_cooldown_seconds
        self.transient_cooldown_seconds = transient_cooldown_seconds
        self.clock = clock
        self._lock = threading.Lock()

    def availability(self, provider_id: str) -> tuple[bool, dict[str, Any] | None]:
        record = self.snapshot().get(provider_id)
        if not record:
            return True, None
        return float(record.get("retry_at", 0)) <= self.clock(), record

    def record_failure(self, provider_id: str, reason: str) -> None:
        cooldown = (
            self.quota_cooldown_seconds
            if reason == "quota_exceeded"
            else self.transient_cooldown_seconds
        )
        now = self.clock()
        with self._lock:
            state = self._read()
            previous = state.get(provider_id, {})
            state[provider_id] = {
                "status": "cooldown",
                "reason": reason,
                "failed_at": now,
                "retry_at": now + cooldown,
                "failure_count": int(previous.get("failure_count", 0)) + 1,
            }
            self._write(state)

    def record_success(self, provider_id: str) -> None:
        with self._lock:
            state = self._read()
            if provider_id not in state:
                return
            state.pop(provider_id)
            self._write(state)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return self._read()

    def _read(self) -> dict[str, dict[str, Any]]:
        if not self.path.is_file():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write(self, state: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)
