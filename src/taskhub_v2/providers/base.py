from typing import Protocol

from taskhub_v2.domain.models import ModelResult, Plan, SupervisionDecision


class ModelProvider(Protocol):
    async def create_plan(self, requirement: str) -> ModelResult[Plan]: ...

    async def review(self, requirement: str, implementation: str) -> ModelResult[str]: ...

    async def assess_risk(
        self, requirement: str, implementation: str
    ) -> ModelResult[str]: ...

    async def supervise(
        self, requirement: str, implementation: str, review: str, risk: str
    ) -> ModelResult[SupervisionDecision]: ...
