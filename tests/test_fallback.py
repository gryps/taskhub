import asyncio
from pathlib import Path

from taskhub_v2.domain.models import ModelResult, Plan
from taskhub_v2.providers.fallback import FallbackModelProvider
from taskhub_v2.providers.health import ProviderHealthStore


class FailingProvider:
    async def create_plan(self, requirement: str):
        raise RuntimeError("quota")


class WorkingProvider:
    async def create_plan(self, requirement: str):
        return ModelResult(
            content=Plan(summary="fallback", steps=["work"], acceptance=["pass"]),
            provider="working",
            model="model",
        )


def test_router_records_failed_provider_before_fallback():
    async def scenario():
        router = FallbackModelProvider(
            {"first": FailingProvider(), "second": WorkingProvider()},
            {"planner": ["first", "second"]},
        )
        result = await router.create_plan("requirement")
        assert result.provider == "working"
        assert result.failed_providers == ["first:RuntimeError"]

    asyncio.run(scenario())


def test_router_skips_cooling_provider_then_retries_preferred(tmp_path: Path):
    async def scenario():
        now = [100.0]
        health = ProviderHealthStore(
            str(tmp_path / "health.json"),
            quota_cooldown_seconds=10,
            transient_cooldown_seconds=10,
            clock=lambda: now[0],
        )
        first = FailingProvider()
        second = WorkingProvider()
        router = FallbackModelProvider(
            {"first": first, "second": second},
            {"planner": ["first", "second"]},
            health,
        )

        initial = await router.create_plan("requirement")
        assert initial.failed_providers == ["first:RuntimeError"]
        cooling = await router.create_plan("requirement")
        assert cooling.failed_providers == ["first:cooldown:RuntimeError"]
        now[0] = 111.0
        recovered = WorkingProvider()
        router.providers["first"] = recovered
        result = await router.create_plan("requirement")
        assert result.provider == "working"
        assert result.failed_providers == []
        assert "first" not in health.snapshot()

    asyncio.run(scenario())
