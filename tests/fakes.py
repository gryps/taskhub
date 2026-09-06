from taskhub_v2.domain.models import (
    ExecutionResult,
    ModelResult,
    Plan,
    SupervisionDecision,
)


class RecordingProvider:
    def __init__(self):
        self.plan_calls = 0
        self.review_calls = 0
        self.risk_calls = 0
        self.supervisor_calls = 0

    async def create_plan(self, requirement: str) -> Plan:
        self.plan_calls += 1
        return ModelResult(
            content=Plan(
                summary=f"Plan for {requirement}",
                steps=["change", "verify"],
                acceptance=["works"],
            ),
            provider="recording",
            model="test",
        )

    async def review(self, requirement: str, implementation: str) -> str:
        self.review_calls += 1
        return ModelResult(content="accepted", provider="recording", model="test")

    async def assess_risk(self, requirement: str, implementation: str) -> str:
        self.risk_calls += 1
        return ModelResult(content="low", provider="recording", model="test")

    async def supervise(self, requirement, implementation, review, risk):
        self.supervisor_calls += 1
        return ModelResult(
            content=SupervisionDecision(
                decision="approve", summary="accepted", reasons=["tests pass"]
            ),
            provider="recording",
            model="test",
        )


class RecordingWorker:
    def __init__(self):
        self.calls = 0

    async def execute(
        self,
        run_id: str,
        project_id: str,
        requirement: str,
        plan: Plan,
        revision: int = 0,
        feedback: str = "",
    ) -> ExecutionResult:
        self.calls += 1
        return ExecutionResult(summary=f"artifact:{run_id}:{project_id}")
