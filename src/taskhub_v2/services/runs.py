from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from langgraph.types import Command

from taskhub_v2.domain.models import (
    ApprovalRequest,
    RunStatus,
    RunView,
    ResumeRequest,
    Stage,
    StartRunRequest,
)


class RunNotFoundError(LookupError):
    pass


class RunConflictError(RuntimeError):
    pass


class RunService:
    def __init__(self, graph, projects=None):
        self.graph = graph
        self.projects = projects

    @staticmethod
    def _config(run_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": run_id}}

    async def start(self, request: StartRunRequest) -> RunView:
        max_revisions = 2
        if self.projects is not None:
            max_revisions = self.projects.get(request.project_id).max_revision_attempts
        run_id = str(uuid4())
        initial = {
            "run_id": run_id,
            "project_id": request.project_id,
            "requirement": request.requirement,
            "requirement_version": 1,
            "current_stage": Stage.INTAKE.value,
            "status": RunStatus.RUNNING.value,
            "plan": None,
            "implementation": None,
            "review": None,
            "risk": None,
            "supervision": None,
            "publication": None,
            "decision": None,
            "attempt": 0,
            "revision_count": 0,
            "max_revision_attempts": max_revisions,
            "revision_feedback": "",
            "pending_action": None,
            "blocking_reason": None,
            "model_runs": [],
            "timeline": [],
        }
        await self.graph.ainvoke(initial, self._config(run_id))
        return await self.get(run_id)

    async def get(self, run_id: str) -> RunView:
        snapshot = await self.graph.aget_state(self._config(run_id))
        values = snapshot.values
        if not values or values.get("run_id") != run_id:
            raise RunNotFoundError(run_id)
        return RunView(
            run_id=run_id,
            project_id=values["project_id"],
            requirement=values["requirement"],
            stage=values["current_stage"],
            status=values["status"],
            next_nodes=list(snapshot.next),
            pending_action=values.get("pending_action"),
            blocking_reason=values.get("blocking_reason"),
            revision_count=values.get("revision_count", 0),
            max_revision_attempts=values.get("max_revision_attempts", 2),
            revision_feedback=values.get("revision_feedback", ""),
            plan=values.get("plan"),
            implementation=values.get("implementation"),
            review=values.get("review"),
            risk=values.get("risk"),
            supervision=values.get("supervision"),
            publication=values.get("publication"),
            model_runs=values.get("model_runs", []),
            timeline=values.get("timeline", []),
        )

    async def approve(self, run_id: str, request: ApprovalRequest) -> RunView:
        current = await self.get(run_id)
        if current.stage != Stage.PLAN_APPROVAL or "plan_approval" not in current.next_nodes:
            raise RunConflictError("run is not waiting for plan approval")
        await self.graph.ainvoke(
            Command(resume=request.model_dump(mode="json")), self._config(run_id)
        )
        return await self.get(run_id)

    async def resume(self, run_id: str, request: ResumeRequest) -> RunView:
        current = await self.get(run_id)
        action = current.pending_action or {}
        if not current.next_nodes or request.decision not in action.get("choices", []):
            raise RunConflictError("decision is not valid for the pending action")
        await self.graph.ainvoke(
            Command(resume=request.model_dump(mode="json")), self._config(run_id)
        )
        return await self.get(run_id)

    async def history(self, run_id: str) -> list[dict[str, Any]]:
        await self.get(run_id)
        history = []
        async for snapshot in self.graph.aget_state_history(self._config(run_id)):
            history.append(
                {
                    "checkpoint_id": snapshot.config["configurable"].get("checkpoint_id"),
                    "stage": snapshot.values.get("current_stage"),
                    "status": snapshot.values.get("status"),
                    "next_nodes": list(snapshot.next),
                }
            )
        return history

    async def watch(self, run_id: str) -> AsyncIterator[RunView]:
        previous = None
        while True:
            current = await self.get(run_id)
            signature = current.model_dump_json()
            if signature != previous:
                yield current
                previous = signature
            if current.status in {RunStatus.COMPLETED, RunStatus.REJECTED, RunStatus.FAILED}:
                return
            import asyncio

            await asyncio.sleep(1)
