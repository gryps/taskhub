from collections.abc import Awaitable, Callable
from typing import Any

from taskhub_v2.domain.models import ModelResult, Plan
from taskhub_v2.providers.base import ModelProvider
from taskhub_v2.providers.health import ProviderHealthStore


class ProvidersExhaustedError(RuntimeError):
    def __init__(self, role: str, failures: list[str]):
        super().__init__(f"all providers failed for {role}: {', '.join(failures)}")
        self.role = role
        self.failures = failures


class FallbackModelProvider:
    def __init__(
        self,
        providers: dict[str, ModelProvider],
        routes: dict[str, list[str]],
        health: ProviderHealthStore | None = None,
    ):
        self.providers = providers
        self.routes = routes
        self.health = health

    async def create_plan(self, requirement: str) -> ModelResult[Plan]:
        return await self._route("planner", lambda provider: provider.create_plan(requirement))

    async def review(self, requirement: str, implementation: str) -> ModelResult[str]:
        return await self._route(
            "reviewer", lambda provider: provider.review(requirement, implementation)
        )

    async def assess_risk(self, requirement: str, implementation: str) -> ModelResult[str]:
        return await self._route(
            "risk", lambda provider: provider.assess_risk(requirement, implementation)
        )

    async def supervise(
        self, requirement: str, implementation: str, review: str, risk: str
    ) -> ModelResult[Any]:
        return await self._route(
            "supervisor",
            lambda provider: provider.supervise(requirement, implementation, review, risk),
        )

    async def close(self) -> None:
        for provider in self.providers.values():
            close = getattr(provider, "close", None)
            if close:
                await close()

    async def _route(
        self,
        role: str,
        operation: Callable[[ModelProvider], Awaitable[ModelResult[Any]]],
    ) -> ModelResult[Any]:
        failures = []
        for provider_id in self.routes[role]:
            available, health = (
                self.health.availability(provider_id) if self.health else (True, None)
            )
            if not available:
                failures.append(f"{provider_id}:cooldown:{health['reason']}")
                continue
            try:
                result = await operation(self.providers[provider_id])
                if self.health and not self.health.record_success(provider_id):
                    failures.append(f"{provider_id}:recovery_probe")
                    continue
                result.failed_providers = failures
                return result
            except Exception as exc:
                reason = getattr(exc, "reason", exc.__class__.__name__)
                if self.health:
                    self.health.record_failure(provider_id, reason)
                diagnostic = " ".join(str(getattr(exc, "diagnostic", "")).split())[:500]
                suffix = f" ({diagnostic})" if diagnostic else ""
                failures.append(f"{provider_id}:{reason}{suffix}")
        raise ProvidersExhaustedError(role, failures)
