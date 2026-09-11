from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from uuid import uuid4

from taskhub_v2.domain.change_request import ChangeRequest, ChangeRequestStatus
from taskhub_v2.domain.production import (
    ExecutionPlan,
    ExecutionPlanStatus,
    ProductionTask,
    ProductionTaskStatus,
    TaskAttempt,
    TaskAttemptStatus,
)
from taskhub_v2.services.revision_impact import affected_descendants, paths_overlap


class RevisionNotFoundError(LookupError):
    pass


class RevisionConflictError(RuntimeError):
    pass


class RevisionService:
    def __init__(self, store, projects, planner):
        self.store = store
        self.projects = projects
        self.planner = planner

    async def list(self, project_id: str) -> list[ChangeRequest]:
        self.projects.get(project_id)
        records = await self.store.list(project_id=project_id, object_type="change_request")
        return sorted(
            (item for item in records if isinstance(item, ChangeRequest)),
            key=lambda item: item.created_at,
            reverse=True,
        )

    async def propose(
        self,
        project_id: str,
        plan_id: str,
        plan_version: int,
        reason: str,
        *,
        actor: str,
        source_event: str = "manual.change_requested",
        affected_task_ids: list[str] | None = None,
        changed_paths: list[str] | None = None,
        automatic: bool = False,
    ) -> ChangeRequest:
        plan, tasks = await self._plan(project_id, plan_id, plan_version)
        requested = set(affected_task_ids or [])
        unknown = requested - {item.task_id for item in tasks}
        if unknown:
            raise RevisionConflictError("unknown affected tasks: " + ", ".join(sorted(unknown)))
        impact_reasons: dict[str, list[str]] = defaultdict(list)
        for task_id in requested:
            impact_reasons[task_id].append("explicitly selected")
        for task in tasks:
            for changed in changed_paths or []:
                if any(paths_overlap(changed, allowed) for allowed in task.allowed_paths):
                    requested.add(task.task_id)
                    impact_reasons[task.task_id].append(f"path changed: {changed}")
            if task.status != ProductionTaskStatus.COMPLETED:
                requested.add(task.task_id)
                impact_reasons[task.task_id].append("task has no reusable completed result")
        if not requested:
            raise RevisionConflictError("change request does not affect any task")
        affected = affected_descendants(tasks, requested, impact_reasons)
        regression = [
            item.task_id
            for item in tasks
            if item.task_id in affected and item.task_type == "verification"
        ]
        existing = [
            item
            for item in await self.list(project_id)
            if item.source_plan_id == plan.plan_id and item.automatic
        ]
        revision_number = len(existing) + 1 if automatic else plan.version
        automatic_allowed = (
            automatic and revision_number <= self.projects.get(project_id).max_revision_attempts
        )
        request = ChangeRequest(
            project_id=project_id,
            change_request_id=f"cr_{uuid4().hex}",
            status=(
                ChangeRequestStatus.APPROVED if automatic_allowed else ChangeRequestStatus.PROPOSED
            ),
            reason=reason,
            source_event=source_event,
            source_run_id=plan.run_id,
            source_plan_id=plan.plan_id,
            source_plan_version=plan.version,
            automatic=automatic,
            revision_number=revision_number,
            affected_task_ids=sorted(affected),
            superseded_task_ids=sorted(affected),
            regression_scope=regression,
            changed_paths=sorted(set(changed_paths or [])),
            impact_reasons=dict(impact_reasons),
            plan_diff={
                "from_version": plan.version,
                "to_version": plan.version + 1,
                "rerun": sorted(affected),
                "reuse": sorted(item.task_id for item in tasks if item.task_id not in affected),
                "requires_manual_approval": automatic and not automatic_allowed,
            },
            approved_by="system" if automatic_allowed else "",
            approved_at=datetime.now(UTC) if automatic_allowed else None,
            created_by=actor,
            source_ids=[plan.plan_id],
        )
        return await self.store.save(request)

    async def approve(self, project_id: str, request_id: str, actor: str) -> ChangeRequest:
        request = await self._request(project_id, request_id)
        if request.status != ChangeRequestStatus.PROPOSED:
            raise RevisionConflictError("only a proposed change request can be approved")
        return await self.store.save(
            request.model_copy(
                update={
                    "status": ChangeRequestStatus.APPROVED,
                    "approved_by": actor,
                    "approved_at": datetime.now(UTC),
                }
            )
        )

    async def reject(self, project_id: str, request_id: str) -> ChangeRequest:
        request = await self._request(project_id, request_id)
        if request.status not in {ChangeRequestStatus.PROPOSED, ChangeRequestStatus.APPROVED}:
            raise RevisionConflictError("applied or rejected change requests are immutable")
        return await self.store.save(
            request.model_copy(update={"status": ChangeRequestStatus.REJECTED})
        )

    async def apply(self, project_id: str, request_id: str, actor: str) -> dict:
        request = await self._request(project_id, request_id)
        if request.status == ChangeRequestStatus.APPLIED:
            plan, tasks = await self._plan(
                project_id, request.source_plan_id, request.source_plan_version + 1
            )
            return {"change_request": request, "execution_plan": plan, "tasks": tasks}
        if request.status != ChangeRequestStatus.APPROVED:
            raise RevisionConflictError("change request must be approved before application")
        previous, old_tasks = await self._plan(
            project_id, request.source_plan_id, request.source_plan_version
        )
        version = previous.version + 1
        if await self.store.get("execution_plan", previous.plan_id, str(version)):
            raise RevisionConflictError("target execution plan version already exists")
        affected = set(request.affected_task_ids)
        id_map = {task.task_id: f"{task.task_id}_v{version}" for task in old_tasks}
        attempts = await self._attempts(project_id, previous.plan_id, previous.version)
        latest = self._latest_validated(attempts)
        revised_tasks = []
        reused_attempts = []
        for task in old_tasks:
            reusable = task.task_id not in affected and task.task_id in latest
            values = task.model_dump()
            values.update(
                task_id=id_map[task.task_id],
                plan_version=version,
                status=(
                    ProductionTaskStatus.COMPLETED if reusable else ProductionTaskStatus.PENDING
                ),
                depends_on=[id_map[item] for item in task.depends_on],
                inputs=self._remap_inputs(task.inputs, id_map),
                outputs=[
                    {
                        **output,
                        "id": self._remap_output(output.get("id", ""), id_map),
                    }
                    for output in task.outputs
                ],
                waiting_reasons=[],
                assigned_node_id=latest[task.task_id].node_id if reusable else "",
                batch_id="",
                supersedes_task_id=task.task_id if not reusable else "",
                reused_from_task_id=task.task_id if reusable else "",
                reused_attempt_id=(latest[task.task_id].attempt_id if reusable else ""),
                created_by=actor,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                content_digest="",
            )
            revised = ProductionTask.model_validate(values)
            revised_tasks.append(revised)
            if reusable:
                reused_attempts.append(
                    self._reuse_attempt(latest[task.task_id], revised, version, actor)
                )
        findings, order = await self.planner.validate(revised_tasks, await self._contract(previous))
        if findings:
            raise RevisionConflictError(
                "revised plan is invalid: " + "; ".join(item.detail for item in findings[:5])
            )
        plan = ExecutionPlan(
            **previous.model_dump(
                exclude={
                    "version",
                    "previous_plan_version",
                    "change_request_id",
                    "status",
                    "task_ids",
                    "milestones",
                    "created_by",
                    "created_at",
                    "updated_at",
                    "content_digest",
                }
            ),
            version=version,
            previous_plan_version=previous.version,
            change_request_id=request.change_request_id,
            status=ExecutionPlanStatus.DRAFT,
            task_ids=[item.task_id for item in revised_tasks],
            milestones=[{"id": "delivery", "task_ids": order}],
            created_by=actor,
        )
        for task in revised_tasks:
            await self.store.save(task)
        for attempt in reused_attempts:
            await self.store.save(attempt)
        plan = await self.store.save(plan)
        plan = await self.store.save(
            plan.model_copy(update={"status": ExecutionPlanStatus.VALIDATING})
        )
        plan = await self.store.save(plan.model_copy(update={"status": ExecutionPlanStatus.ACTIVE}))
        if previous.status == ExecutionPlanStatus.ACTIVE:
            await self.store.save(
                previous.model_copy(update={"status": ExecutionPlanStatus.SUPERSEDED})
            )
        request = await self.store.save(
            request.model_copy(
                update={
                    "status": ChangeRequestStatus.APPLIED,
                    "added_task_ids": [
                        item.task_id for item in revised_tasks if item.supersedes_task_id
                    ],
                    "applied_at": datetime.now(UTC),
                    "plan_diff": {
                        **request.plan_diff,
                        "task_id_map": id_map,
                        "reused_attempts": [item.attempt_id for item in reused_attempts],
                    },
                }
            )
        )
        return {"change_request": request, "execution_plan": plan, "tasks": revised_tasks}

    async def automatic_failure(self, plan, task, reason: str) -> ChangeRequest:
        return await self.propose(
            plan.project_id,
            plan.plan_id,
            plan.version,
            reason,
            actor="scheduler",
            source_event="task.attempts_exhausted",
            affected_task_ids=[task.task_id],
            automatic=True,
        )

    async def prepare_automatic(
        self,
        project_id: str,
        plan_id: str,
        plan_version: int,
        reason: str,
        *,
        actor: str = "workflow-governance",
    ) -> dict:
        _plan, tasks = await self._plan(project_id, plan_id, plan_version)
        seeds = [item.task_id for item in tasks if item.status == ProductionTaskStatus.BLOCKED]
        if not seeds:
            seeds = [item.task_id for item in tasks if item.task_type == "implementation"]
        request = await self.propose(
            project_id,
            plan_id,
            plan_version,
            reason,
            actor=actor,
            source_event="workflow.revision_requested",
            affected_task_ids=seeds,
            automatic=True,
        )
        if request.status == ChangeRequestStatus.PROPOSED:
            raise RevisionConflictError(
                "automatic revision limit reached; manual approval is required"
            )
        return await self.apply(project_id, request.change_request_id, actor)

    async def _plan(self, project_id, plan_id, version):
        plan = await self.store.get("execution_plan", plan_id, str(version))
        if not isinstance(plan, ExecutionPlan) or plan.project_id != project_id:
            raise RevisionNotFoundError(plan_id)
        records = await self.store.list(project_id=project_id, object_type="task")
        tasks = [
            item
            for item in records
            if isinstance(item, ProductionTask)
            and item.plan_id == plan_id
            and item.plan_version == version
        ]
        return plan, tasks

    async def _request(self, project_id, request_id):
        record = await self.store.get("change_request", request_id, "1")
        if not isinstance(record, ChangeRequest) or record.project_id != project_id:
            raise RevisionNotFoundError(request_id)
        return record

    async def _contract(self, plan):
        record = await self.store.get(
            "project_contract", plan.project_contract_id, str(plan.project_contract_version)
        )
        if record is None:
            raise RevisionConflictError("frozen project contract is missing")
        return record

    async def _attempts(self, project_id, plan_id, version):
        records = await self.store.list(project_id=project_id, object_type="task_attempt")
        return [
            item
            for item in records
            if isinstance(item, TaskAttempt)
            and item.plan_id == plan_id
            and item.plan_version == version
        ]

    @staticmethod
    def _latest_validated(attempts):
        latest = {}
        for attempt in attempts:
            if attempt.status == TaskAttemptStatus.VALIDATED and attempt.result:
                current = latest.get(attempt.task_id)
                if current is None or attempt.attempt_number > current.attempt_number:
                    latest[attempt.task_id] = attempt
        return latest

    @staticmethod
    def _reuse_attempt(source, task, version, actor):
        return TaskAttempt(
            project_id=task.project_id,
            attempt_id=f"attempt_{task.task_id.removeprefix('task_')}_reuse",
            task_id=task.task_id,
            attempt_number=1,
            status=TaskAttemptStatus.VALIDATED,
            node_id=source.node_id,
            result=source.result,
            evidence_ids=source.evidence_ids,
            plan_id=task.plan_id,
            plan_version=version,
            idempotency_key=source.idempotency_key,
            base_commit=source.base_commit,
            started_at=source.started_at,
            finished_at=source.finished_at,
            reused_from_attempt_id=source.attempt_id,
            created_by=actor,
            source_ids=[source.attempt_id],
        )

    @staticmethod
    def _remap_inputs(inputs, id_map):
        return [
            {
                **item,
                "source_task_id": id_map.get(
                    item.get("source_task_id"), item.get("source_task_id")
                ),
                "source_output": RevisionService._remap_output(
                    item.get("source_output", ""), id_map
                ),
            }
            for item in inputs
        ]

    @staticmethod
    def _remap_output(value, id_map):
        for old, new in id_map.items():
            if value.startswith(old + ":"):
                return new + value[len(old) :]
        return value
