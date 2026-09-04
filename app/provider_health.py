from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from typing import Any


TRANSIENT_REASONS = {
    "busy",
    "needs_reauth",
    "network_error",
    "provider_error",
    "quota_exceeded",
    "rate_limited",
    "runner_error",
    "timeout",
}

_LOCK = threading.Lock()
_STATE: dict[str, dict[str, Any]] = {}


def _iso(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def _cooldown_seconds(reason: str) -> int:
    specific = os.getenv(f"PROVIDER_COOLDOWN_{reason.upper()}_SECONDS")
    default = os.getenv("PROVIDER_COOLDOWN_SECONDS", "60")
    try:
        return max(1, int(specific or default))
    except ValueError:
        return 60


def before_attempt(provider: str, now: float | None = None) -> tuple[bool, dict[str, Any]]:
    timestamp = time.time() if now is None else now
    with _LOCK:
        state = _STATE.get(provider)
        if not state or state.get("status") == "healthy":
            return True, {"status": "healthy", "event": "normal"}
        retry_at = float(state.get("retry_at") or 0)
        if timestamp < retry_at:
            return False, {
                "status": "cooldown",
                "event": "fallback_active",
                "reason": state.get("reason", "provider_unavailable"),
                "retry_at": _iso(retry_at),
            }
        state["status"] = "probing"
        state["last_probe_at"] = timestamp
        return True, {
            "status": "probing",
            "event": "recovery_probe",
            "previous_reason": state.get("reason", "provider_unavailable"),
        }


def record_failure(provider: str, reason: str, now: float | None = None) -> dict[str, Any]:
    timestamp = time.time() if now is None else now
    if reason not in TRANSIENT_REASONS:
        return {"status": "healthy", "event": "not_degraded", "reason": reason}
    retry_at = timestamp + _cooldown_seconds(reason)
    with _LOCK:
        previous = _STATE.get(provider, {})
        state = {
            "provider": provider,
            "status": "degraded",
            "reason": reason,
            "failure_count": int(previous.get("failure_count", 0)) + 1,
            "failed_at": timestamp,
            "retry_at": retry_at,
        }
        _STATE[provider] = state
    return {
        "status": "degraded",
        "event": "fallback_started",
        "reason": reason,
        "failure_count": state["failure_count"],
        "retry_at": _iso(retry_at),
    }


def record_success(provider: str, now: float | None = None) -> dict[str, Any]:
    timestamp = time.time() if now is None else now
    with _LOCK:
        previous = _STATE.get(provider)
        recovered = bool(previous and previous.get("status") in {"degraded", "probing"})
        _STATE[provider] = {
            "provider": provider,
            "status": "healthy",
            "reason": "ok",
            "failure_count": 0,
            "recovered_at": timestamp if recovered else previous.get("recovered_at") if previous else None,
        }
    return {
        "status": "healthy",
        "event": "recovered_to_preferred" if recovered else "success",
        "recovered_at": _iso(timestamp) if recovered else None,
    }


def provider_health() -> list[dict[str, Any]]:
    with _LOCK:
        result = []
        for provider, state in sorted(_STATE.items()):
            result.append(
                {
                    "provider": provider,
                    "status": state.get("status", "healthy"),
                    "reason": state.get("reason", "ok"),
                    "failure_count": state.get("failure_count", 0),
                    "failed_at": _iso(state.get("failed_at")),
                    "retry_at": _iso(state.get("retry_at")),
                    "recovered_at": _iso(state.get("recovered_at")),
                }
            )
        return result


def reset_provider_health() -> None:
    with _LOCK:
        _STATE.clear()
