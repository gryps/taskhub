from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from langgraph.types import Command

from taskhub_v2.domain.models import (
    AcceptanceSubmission,
    ApprovalRequest,
    ResumeRequest,
    RunStatus,
    RunView,
    Stage,
    StartRunRequest,
)
from taskhub_v2.services.task_state import NODE_STAGES, checkpoint_values, workflow_steps

logger = logging.getLogger(__name__)


class RunNotFoundError(LookupError):
    pass


class RunConflictError(RuntimeError):
    pass


class RunService:
    def __init__(self, graph, projects=None, task_index=None):
        self.graph = graph
        self.projects = projects
        self.task_index = task_index

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
            "production_line": request.production_line,
            "requirement": request.requirement,
            "requirement_version": 1,
            "current_stage": Stage.INTAKE.value,
            "status": RunStatus.RUNNING.value,
            "plan": None,
            "implementation": None,
            "acceptance": None,
            "review": None,
            "risk": None,
            "supervision": None,
            "publication": None,
            "decision": None,
            "attempt": 0,
            "revision_count": 0,
            "max_revision_attempts": max_revisions,
            "revision_feedback": "",
            "acceptance_contract_bootstrap_attempted": False,
            "pending_action": None,
            "blocking_reason": None,
            "model_runs": [],
            "timeline": [],
        }
        await self._execute(run_id, initial)
        return await self.get(run_id, production_line=request.production_line, sync=True)

    async def get(
        self, run_id: str, production_line: str | None = None, sync: bool = False
    ) -> RunView:
        snapshot = await self.graph.aget_state(self._config(run_id))
        values = checkpoint_values(snapshot)
        if not values or values.get("run_id") != run_id:
            raise RunNotFoundError(run_id)
        indexed = await self.task_index.get(run_id) if self.task_index else None
        if self.task_index and (sync or indexed is None):
            indexed = await self.task_index.upsert(values, production_line)
        effective_project = (
            indexed.rebound_project_id
            if indexed and indexed.rebound_project_id
            else values["project_id"]
        )
        missing = self._project_missing(effective_project)
        return RunView(
            run_id=run_id,
            project_id=effective_project,
            original_project_id=(
                values["project_id"] if effective_project != values["project_id"] else None
            ),
            requirement=values["requirement"],
            production_line=values.get("production_line") or (
                indexed.production_line if indexed else "default"
            ),
            created_at=indexed.created_at if indexed else None,
            updated_at=indexed.updated_at if indexed else None,
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
            acceptance=values.get("acceptance"),
            review=values.get("review"),
            risk=values.get("risk"),
            supervision=values.get("supervision"),
            publication=values.get("publication"),
            model_runs=values.get("model_runs", []),
            timeline=values.get("timeline", []),
            workflow_steps=workflow_steps(values),
            archived_at=indexed.archived_at if indexed else None,
            orphaned=missing,
            project_missing=missing,
            allowed_actions=["archive", "rebind_project"] if missing else [],
        )

    async def list(self, **filters):
        page = await self.task_index.list(**filters)
        return page.model_copy(update={"items": [self._project_task(item) for item in page.items]})

    def _project_missing(self, project_id: str) -> bool:
        if self.projects is None:
            return False
        try:
            self.projects.get(project_id)
            return False
        except LookupError:
            return True

    def _project_task(self, item):
        effective = item.rebound_project_id or item.project_id
        missing = self._project_missing(effective)
        return item.model_copy(update={
            "project_id": effective,
            "original_project_id": item.project_id if effective != item.project_id else None,
            "orphaned": missing, "project_missing": missing,
            "allowed_actions": ["archive", "rebind_project"] if missing else [],
        })

    async def archive(self, run_id: str):
        await self.get(run_id)
        item = await self.task_index.archive(run_id)
        if item is None:
            raise RunNotFoundError(run_id)
        return self._project_task(item)

    async def rebind(self, run_id: str, project_id: str) -> RunView:
        current = await self.get(run_id)
        if current.archived_at:
            raise RunConflictError("task is archived; project rebinding is disabled")
        if self.projects is None or self._project_missing(project_id):
            raise RunConflictError("target project is not registered")
        await self.task_index.rebind(run_id, project_id)
        return await self.get(run_id)

    async def backfill(self, checkpointer) -> None:
        """Repair missing and stale rows from authoritative latest checkpoints."""
        latest: dict[str, dict[str, Any]] = {}
        async for item in checkpointer.alist(None):
            values = item.checkpoint.get("channel_values", {})
            run_id = values.get("run_id")
            if not run_id or run_id in latest or not values.get("project_id"):
                continue
            latest[run_id] = values
        # PostgreSQL uses one async connection for the listing cursor. Finish
        # that cursor before querying individual graph snapshots.
        for run_id, values in latest.items():
            snapshot = await self.graph.aget_state(self._config(run_id))
            await self.task_index.upsert(checkpoint_values(snapshot),
                                         values.get("production_line"))

    async def recover_interrupted(self) -> None:
        """Replay graph-owned running checkpoints after a controller restart."""
        if self.task_index is None:
            return
        page = await self.task_index.list(status=RunStatus.RUNNING, page_size=100)
        for item in page.items:
            try:
                current = await self.get(item.run_id)
                if current.pending_action or not current.next_nodes or current.archived_at:
                    continue
                await self.replay(item.run_id)
            except Exception:
                logger.exception("failed to recover interrupted run %s", item.run_id)

    async def _execute(self, run_id, payload):
        values = payload if isinstance(payload, dict) else None
        if values is not None and self.task_index:
            await self.task_index.upsert(values, values.get("production_line"))
        try:
            async for mode, data in self.graph.astream(
                payload, self._config(run_id), stream_mode=["values", "debug"]
            ):
                if mode == "values":
                    values = dict(data)
                elif data.get("type") == "task" and values:
                    node = data["payload"]["name"]
                    if values.get("status") == "running":
                        values = dict(values, current_stage=NODE_STAGES.get(node, node))
                if values and self.task_index:
                    await self.task_index.upsert(values, values.get("production_line"))
        finally:
            snapshot = await self.graph.aget_state(self._config(run_id))
            if snapshot.values and self.task_index:
                await self.task_index.upsert(checkpoint_values(snapshot),
                                             snapshot.values.get("production_line"))

    async def approve(self, run_id: str, request: ApprovalRequest) -> RunView:
        current = await self.get(run_id)
        if current.archived_at:
            raise RunConflictError("task is archived; workflow actions are disabled")
        if request.decision == "approve" and current.project_missing:
            raise RunConflictError(
                "project is not registered; archive the task or rebind it to an existing project"
            )
        choices = (current.pending_action or {}).get("choices", [])
        if (
            current.stage != Stage.PLAN_APPROVAL
            or "plan_approval" not in current.next_nodes
            or request.decision not in choices
        ):
            raise RunConflictError("run is not waiting for plan approval")
        update = {"project_id": current.project_id} if current.original_project_id else None
        await self._execute(
            run_id, Command(resume=request.model_dump(mode="json"), update=update)
        )
        return await self.get(run_id, sync=True)

    async def resume(self, run_id: str, request: ResumeRequest) -> RunView:
        current = await self.get(run_id)
        if current.archived_at:
            raise RunConflictError("task is archived; workflow actions are disabled")
        if request.decision in {"retry", "approve", "reassess"} and current.project_missing:
            raise RunConflictError(
                "project is not registered; archive the task or rebind it to an existing project"
            )
        action = current.pending_action or {}
        choices = list(action.get("choices", []))
        # Checkpoints created before acceptance revisions existed only contain
        # retry/cancel. Allow them to use the new recovery route after deployment.
        if action.get("type") == "acceptance_recovery" and "revise" not in choices:
            choices.append("revise")
        if action.get("type") == "revision_limit" and "manual" not in choices:
            choices.append("manual")
        if (
            action.get("type") == "revision_limit"
            and current.supervision
            and current.supervision.missing_evidence
            and "recheck" not in choices
        ):
            choices.append("recheck")
        if not current.next_nodes or request.decision not in choices:
            raise RunConflictError("decision is not valid for the pending action")
        update = {"project_id": current.project_id} if current.original_project_id else None
        await self._execute(run_id, Command(resume=request.model_dump(mode="json"), update=update))
        return await self.get(run_id, sync=True)

    async def replay(self, run_id: str) -> RunView:
        current = await self.get(run_id)
        if current.archived_at:
            raise RunConflictError("task is archived; workflow actions are disabled")
        if current.project_missing:
            raise RunConflictError(
                "project is not registered; archive the task or rebind it to an existing project"
            )
        if current.status != RunStatus.RUNNING:
            raise RunConflictError("only running tasks can replay the current stage")
        if current.pending_action:
            raise RunConflictError("task is waiting for an explicit owner action")
        if not current.next_nodes:
            raise RunConflictError("task has no checkpoint node to replay")
        update = {"project_id": current.project_id} if current.original_project_id else None
        payload = Command(update=update) if update else None
        await self._execute(run_id, payload)
        return await self.get(run_id, sync=True)

    async def submit_acceptance(
        self, run_id: str, request: AcceptanceSubmission
    ) -> RunView:
        current = await self.get(run_id)
        if current.archived_at:
            raise RunConflictError("task is archived; workflow actions are disabled")
        if current.project_missing:
            raise RunConflictError(
                "project is not registered; archive the task or rebind it to an existing project"
            )
        if current.stage != Stage.SUPERVISION or "revision_limit" not in current.next_nodes:
            raise RunConflictError("run is not waiting for acceptance evidence")
        payload = {
            "decision": "reassess",
            "evidence": [item.model_dump(mode="json") for item in request.evidence],
        }
        update = {"project_id": current.project_id} if current.original_project_id else None
        await self._execute(run_id, Command(resume=payload, update=update))
        return await self.get(run_id, sync=True)

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
