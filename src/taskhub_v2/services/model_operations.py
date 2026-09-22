from __future__ import annotations

from typing import Any


class ModelOperationsService:
    """Aggregate provider runtime health and checkpoint-owned model traces."""

    def __init__(self, catalog, runs):
        self.catalog = catalog
        self.runs = runs

    async def report(self, *, project_id="", recent_runs=50) -> dict[str, Any]:
        catalog = await self.catalog.status()
        task_page = await self.runs.list(
            project_id=project_id,
            page=1,
            page_size=recent_runs,
        )
        aggregates: dict[tuple[str, str], dict[str, Any]] = {}
        role_counts: dict[str, int] = {}
        total_duration = 0
        fallback_events = 0
        invocations = 0
        for task in task_page.items:
            run = await self.runs.get(task.run_id)
            for trace in run.model_runs:
                key = (trace.provider, trace.model)
                item = aggregates.setdefault(key, {
                    "provider": trace.provider,
                    "model": trace.model,
                    "invocations": 0,
                    "duration_ms": 0,
                    "fallback_events": 0,
                    "roles": {},
                })
                item["invocations"] += 1
                item["duration_ms"] += trace.duration_ms
                item["fallback_events"] += len(trace.failed_providers)
                item["roles"][trace.role] = item["roles"].get(trace.role, 0) + 1
                role_counts[trace.role] = role_counts.get(trace.role, 0) + 1
                invocations += 1
                total_duration += trace.duration_ms
                fallback_events += len(trace.failed_providers)
        usage = sorted(
            ({
                **item,
                "average_duration_ms": round(item["duration_ms"] / item["invocations"]),
            } for item in aggregates.values()),
            key=lambda item: (-item["invocations"], item["provider"], item["model"]),
        )
        providers = [self._provider(item) for item in catalog.get("providers", [])]
        unhealthy = sum(item["operational_state"] != "healthy" for item in providers)
        return {
            "summary": {
                "providers": len(providers),
                "unhealthy_providers": unhealthy,
                "invocations": invocations,
                "fallback_events": fallback_events,
                "average_duration_ms": round(total_duration / invocations) if invocations else 0,
                "sampled_runs": len(task_page.items),
                "available_runs": task_page.total,
            },
            "providers": providers,
            "usage": usage,
            "role_counts": role_counts,
            "role_models": catalog.get("role_models", {}),
        }

    @staticmethod
    def _provider(provider: dict[str, Any]) -> dict[str, Any]:
        runtime = provider.get("runtime_health") or {}
        configured = bool(provider.get("configured"))
        status = str(provider.get("status", "unknown"))
        if provider.get("enabled") is False:
            operational_state = "disabled"
        elif runtime.get("status") in {"cooldown", "recovering", "degraded"}:
            operational_state = runtime["status"]
        elif configured and status not in {"unauthenticated", "not_configured"}:
            operational_state = "healthy"
        else:
            operational_state = "unconfigured"
        billing = provider.get("billing") or {}
        return {
            "id": provider.get("id", "unknown"),
            "display_name": provider.get("display_name", ""),
            "kind": provider.get("kind", "unknown"),
            "model": provider.get("model", ""),
            "configured": configured,
            "enabled": provider.get("enabled", True),
            "operational_state": operational_state,
            "reason": runtime.get("reason", ""),
            "failure_count": int(runtime.get("failure_count", 0)),
            "recovery_count": int(runtime.get("recovery_count", 0)),
            "retry_at": runtime.get("retry_at"),
            "billing": {
                "kind": billing.get("kind", "unknown"),
                "status": billing.get("status", "unavailable"),
                "detail": billing.get("detail", ""),
                "metrics": billing.get("metrics", []),
            },
        }
