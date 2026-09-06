from taskhub_v2.domain.models import ModelResult, Plan, SupervisionDecision


class DeterministicProvider:
    """Offline provider for workflow verification without consuming model quota."""

    async def create_plan(self, requirement: str) -> ModelResult[Plan]:
        subject = requirement.strip().splitlines()[0][:120]
        return ModelResult(
            content=Plan(
                summary=f"Implement: {subject}",
                steps=["Confirm scope", "Implement in an isolated workspace", "Run verification"],
                acceptance=["Requested behavior is present", "Automated verification passes"],
            ),
            provider="deterministic",
            model="offline",
        )

    async def review(self, requirement: str, implementation: str) -> ModelResult[str]:
        return ModelResult(
            content="accepted: implementation evidence is available",
            provider="deterministic",
            model="offline",
        )

    async def assess_risk(self, requirement: str, implementation: str) -> ModelResult[str]:
        return ModelResult(
            content="low: phase-one deterministic execution",
            provider="deterministic",
            model="offline",
        )

    async def supervise(
        self, requirement: str, implementation: str, review: str, risk: str
    ) -> ModelResult[SupervisionDecision]:
        return ModelResult(
            content=SupervisionDecision(
                decision="approve",
                summary="Offline workflow evidence accepted",
                reasons=["Implementation, review, and risk stages completed"],
            ),
            provider="deterministic",
            model="offline",
        )
