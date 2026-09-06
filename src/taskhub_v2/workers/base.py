from typing import Protocol

from taskhub_v2.domain.models import AcceptanceResult, ExecutionResult, Plan, PublicationResult


class WorkerGateway(Protocol):
    async def execute(
        self,
        run_id: str,
        project_id: str,
        requirement: str,
        plan: Plan,
        revision: int = 0,
        feedback: str = "",
    ) -> ExecutionResult: ...


class PublisherGateway(Protocol):
    async def publish(
        self, run_id: str, project_id: str, implementation: ExecutionResult
    ) -> PublicationResult: ...


class AcceptanceGateway(Protocol):
    async def verify(
        self, run_id: str, project_id: str, implementation: ExecutionResult
    ) -> AcceptanceResult: ...
