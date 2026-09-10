import json
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any


class ProviderHealthStore:
    """Small operational state store; workflow truth remains in LangGraph checkpoints."""

    def __init__(
        self,
        path: str,
        quota_cooldown_seconds: int = 3600,
        transient_cooldown_seconds: int = 60,
        clock: Callable[[], float] = time.time,
        failure_threshold: int = 3,
        recovery_threshold: int = 3,
        probe_interval_seconds: int = 30,
        switch_lock_seconds: int = 300,
    ):
        self.path = Path(path)
        self.quota_cooldown_seconds = quota_cooldown_seconds
        self.transient_cooldown_seconds = transient_cooldown_seconds
        self.clock = clock
        self.failure_threshold = failure_threshold
        self.recovery_threshold = recovery_threshold
        self.probe_interval_seconds = probe_interval_seconds
        self.switch_lock_seconds = switch_lock_seconds
        self._lock = threading.Lock()

    def availability(self, provider_id: str) -> tuple[bool, dict[str, Any] | None]:
        record = self.snapshot().get(provider_id)
        if not record:
            return True, None
        if record.get("status") not in {"cooldown", "recovering"}:
            return True, record
        return float(record.get("retry_at", 0)) <= self.clock(), record

    def record_failure(self, provider_id: str, reason: str) -> None:
        if not self._is_service_failure(reason):
            return
        cooldown = (
            self.quota_cooldown_seconds
            if reason == "quota_exceeded"
            else self.transient_cooldown_seconds
        )
        now = self.clock()
        with self._lock:
            state = self._read()
            previous = state.get(provider_id, {})
            failure_count = int(previous.get("failure_count", 0)) + 1
            tripped = failure_count >= self.failure_threshold
            state[provider_id] = {
                "status": "cooldown" if tripped else "degraded",
                "reason": reason,
                "failed_at": now,
                "retry_at": now + max(cooldown, self.switch_lock_seconds) if tripped else 0,
                "failure_count": failure_count,
                "recovery_count": 0,
            }
            self._write(state)
            self._append_event(provider_id, "circuit_open" if tripped else "failure", reason)

    def record_success(self, provider_id: str) -> bool:
        with self._lock:
            state = self._read()
            if provider_id not in state:
                return True
            previous = state[provider_id]
            if previous.get("status") == "degraded":
                state.pop(provider_id)
                self._write(state)
                self._append_event(provider_id, "healthy", "successful request")
                return True
            recovery_count = int(previous.get("recovery_count", 0)) + 1
            if recovery_count >= self.recovery_threshold:
                state.pop(provider_id)
                self._write(state)
                self._append_event(provider_id, "primary_restored", "recovery threshold passed")
                return True
            state[provider_id] = {
                **previous,
                "status": "recovering",
                "recovery_count": recovery_count,
                "retry_at": self.clock() + self.probe_interval_seconds,
            }
            self._write(state)
            self._append_event(
                provider_id,
                "recovery_probe",
                f"{recovery_count}/{self.recovery_threshold} successful probes",
            )
            return False

    @staticmethod
    def _is_service_failure(reason: str) -> bool:
        value = reason.lower()
        return any(
            marker in value
            for marker in (
                "quota", "rate_limit", "provider_unavailable", "timeout", "connection",
                "network", "readerror", "connecterror", "http_5",
            )
        )

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

    def _append_event(self, provider_id: str, event: str, detail: str) -> None:
        event_path = self.path.with_suffix(self.path.suffix + ".events.jsonl")
        event_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = {
            "at": self.clock(),
            "provider_id": provider_id,
            "event": event,
            "detail": detail[:200],
        }
        with event_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, sort_keys=True) + "\n")
        os.chmod(event_path, 0o600)
