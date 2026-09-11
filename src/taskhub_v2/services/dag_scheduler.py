from __future__ import annotations

import asyncio

from taskhub_v2.domain.dag import ExecutionBatch, ExecutionBatchStatus
from taskhub_v2.domain.models import ExecutionResult
from taskhub_v2.domain.production import (
    ExecutionPlan,
    ExecutionPlanStatus,
    ProductionTask,
    ProductionTaskStatus,
    TaskAttempt,
    TaskAttemptStatus,
)
from taskhub_v2.persistence.production import ProductionStore
from taskhub_v2.services.dag_readiness import (
    add_selection_waiting_reasons,
    select_non_conflicting,
    waiting_reasons,
)
from taskhub_v2.services.dag_scheduler_models import (
    DagExecutionError,
    DagExecutionOutcome,
    DagTaskExecutor,
    idempotency_key,
    lock_conflicts,
    now,
)
from taskhub_v2.services.dag_scheduler_state import save_snapshot
from taskhub_v2.services.fair_budget import FairProjectBudget


class PersistentDagScheduler:
    def __init__(
        self,
        store: ProductionStore,
        executor: DagTaskExecutor,
        *,
        global_concurrency: int = 4,
        project_concurrency: int = 2,
        max_attempts: int = 2,
    ):
        self.store = store
        self.executor = executor
        self.budget = FairProjectBudget(global_concurrency, project_concurrency)
        self.default_project_concurrency = project_concurrency
        self.max_attempts = max_attempts
        self.plan_locks: dict[str, asyncio.Lock] = {}
        self.resource_guard = asyncio.Lock()
        self.active_locks: dict[str, str] = {}

    async def execute(self, plan_id: str, version: int = 1) -> DagExecutionOutcome:
        key = f"{plan_id}:{version}"
        async with self.plan_locks.setdefault(key, asyncio.Lock()):
            plan = await self._plan(plan_id, version)
            tasks = await self._tasks(plan)
            batches = await self._batches(plan)
            base_commit = await self._current_base(plan)
            while not all(item.status == ProductionTaskStatus.COMPLETED for item in tasks):
                tasks = await self._tasks(plan)
                if all(item.status == ProductionTaskStatus.COMPLETED for item in tasks):
                    break
                completed = {
                    item.task_id for item in tasks if item.status == ProductionTaskStatus.COMPLETED
                }
                blocked = [item for item in tasks if item.status == ProductionTaskStatus.BLOCKED]
                if blocked:
                    snapshot = await self._snapshot(plan, tasks, batches, "blocked")
                    raise DagExecutionError(
                        "任务已阻塞：" + ", ".join(item.task_id for item in blocked), snapshot
                    )
                reasons = await waiting_reasons(
                    plan,
                    tasks,
                    completed,
                    self.active_locks,
                    getattr(self.executor, "capability_reason", None),
                )
                candidates = [
                    item
                    for item in sorted(tasks, key=lambda task: (task.priority, task.task_id))
                    if not reasons[item.task_id]
                    and item.status
                    in {
                        ProductionTaskStatus.PENDING,
                        ProductionTaskStatus.READY,
                        ProductionTaskStatus.ASSIGNED,
                        ProductionTaskStatus.RUNNING,
                        ProductionTaskStatus.VERIFYING,
                    }
                ]
                limit = int(
                    plan.policy.get("project_concurrency", self.default_project_concurrency)
                )
                selected = select_non_conflicting(candidates, limit, self.active_locks)
                add_selection_waiting_reasons(candidates, selected, reasons)
                for task in tasks:
                    updated_reasons = reasons[task.task_id]
                    if task.status == ProductionTaskStatus.PENDING and not updated_reasons:
                        task = await self._save_task(task, status=ProductionTaskStatus.READY)
                    if task.status in {ProductionTaskStatus.PENDING, ProductionTaskStatus.READY}:
                        await self._save_task(task, waiting_reasons=updated_reasons)
                refreshed = {item.task_id: item for item in await self._tasks(plan)}
                selected = [refreshed[item.task_id] for item in selected]
                if not selected:
                    snapshot = await self._snapshot(plan, tasks, batches, "waiting", reasons)
                    raise DagExecutionError("没有任务满足 Ready 条件", snapshot)
                sequence = len(batches) + 1
                batch = ExecutionBatch(
                    project_id=plan.project_id,
                    batch_id=f"batch_{plan.plan_id.removeprefix('plan_')}_{sequence:03d}",
                    plan_id=plan.plan_id,
                    plan_version=plan.version,
                    sequence=sequence,
                    task_ids=[item.task_id for item in selected],
                    waiting_reasons={key: value for key, value in reasons.items() if value},
                    selection_reasons={
                        item.task_id: "依赖、合同、资源锁、能力和并发预算均满足"
                        for item in selected
                    },
                    created_by="scheduler",
                )
                batch = await self.store.save(batch)
                batch = await self.store.save(
                    batch.model_copy(
                        update={"status": ExecutionBatchStatus.RUNNING, "started_at": now()}
                    )
                )
                await self._claim_locks(selected)
                await self._snapshot(plan, tasks, [*batches, batch], "running", reasons)
                try:
                    results = await asyncio.gather(
                        *(self._execute_one(plan, task, base_commit, limit) for task in selected),
                        return_exceptions=True,
                    )
                    failures = [item for item in results if isinstance(item, BaseException)]
                    if failures:
                        batch = await self.store.save(
                            batch.model_copy(
                                update={"status": ExecutionBatchStatus.FAILED, "finished_at": now()}
                            )
                        )
                        batches.append(batch)
                        tasks = await self._tasks(plan)
                        snapshot = await self._snapshot(plan, tasks, batches, "blocked")
                        raise DagExecutionError(str(failures[0]), snapshot) from failures[0]
                    completed_results = [item for item in results if isinstance(item, tuple)]
                    base_commit = await self._integrate_batch(plan, completed_results, base_commit)
                    assignments = {}
                    for task, attempt, _result in completed_results:
                        attempt = await self.store.save(
                            attempt.model_copy(
                                update={
                                    "status": TaskAttemptStatus.VALIDATED,
                                    "finished_at": now(),
                                }
                            )
                        )
                        await self._save_task(
                            task,
                            status=ProductionTaskStatus.COMPLETED,
                            assigned_node_id=attempt.node_id,
                            batch_id=batch.batch_id,
                            waiting_reasons=[],
                        )
                        assignments[task.task_id] = attempt.node_id
                    selection_reasons = dict(batch.selection_reasons)
                    for task, attempt, _result in completed_results:
                        selection_reasons[task.task_id] = (
                            f"节点 {attempt.node_id}：健康、能力、槽位与优先级匹配"
                        )
                    batch = await self.store.save(
                        batch.model_copy(
                            update={
                                "status": ExecutionBatchStatus.COMPLETED,
                                "finished_at": now(),
                                "assignments": assignments,
                                "selection_reasons": selection_reasons,
                            }
                        )
                    )
                    batches.append(batch)
                finally:
                    await self._release_locks(selected)
            tasks = await self._tasks(plan)
            plan = await self.store.save(
                plan.model_copy(update={"status": ExecutionPlanStatus.COMPLETED})
            )
            attempts = await self._attempts(plan)
            snapshot = await self._snapshot(plan, tasks, batches, "completed")
            implementation = await self._finalize(plan, tasks, attempts, base_commit)
            return DagExecutionOutcome(
                plan=plan,
                tasks=tasks,
                batches=batches,
                attempts=attempts,
                snapshot=snapshot,
                implementation=implementation,
            )

    async def _execute_one(
        self, plan: ExecutionPlan, task: ProductionTask, base_commit: str, project_limit: int
    ) -> tuple[ProductionTask, TaskAttempt, ExecutionResult]:
        existing = await self._successful_attempt(task)
        if existing and existing.result:
            return task, existing, ExecutionResult.model_validate(existing.result)
        attempts = [item for item in await self._attempts(plan) if item.task_id == task.task_id]
        last_error = ""
        for number in range(len(attempts) + 1, self.max_attempts + 1):
            attempt = TaskAttempt(
                project_id=plan.project_id,
                attempt_id=f"attempt_{task.task_id.removeprefix('task_')}_{number:02d}",
                task_id=task.task_id,
                attempt_number=number,
                plan_id=plan.plan_id,
                plan_version=plan.version,
                base_commit=base_commit,
                idempotency_key=idempotency_key(task, base_commit),
                created_by="scheduler",
            )
            attempt = await self.store.save(attempt)
            attempt = await self.store.save(
                attempt.model_copy(update={"status": TaskAttemptStatus.DISPATCHED})
            )
            if task.status == ProductionTaskStatus.READY:
                task = await self._save_task(task, status=ProductionTaskStatus.ASSIGNED)
            if task.status == ProductionTaskStatus.ASSIGNED:
                task = await self._save_task(task, status=ProductionTaskStatus.RUNNING)
            attempt = await self.store.save(
                attempt.model_copy(
                    update={"status": TaskAttemptStatus.RUNNING, "started_at": now()}
                )
            )
            try:
                async with self.budget.slot(plan.project_id, project_limit):
                    result = await self.executor.execute(task, attempt, base_commit=base_commit)
                node_id = result.coding_node or result.execution_node or "controller-local"
                attempt = await self.store.save(
                    attempt.model_copy(
                        update={
                            "status": TaskAttemptStatus.RESULT_RECEIVED,
                            "node_id": node_id,
                            "result": result.model_dump(mode="json"),
                        }
                    )
                )
                task = await self._save_task(
                    task,
                    status=ProductionTaskStatus.VERIFYING,
                    assigned_node_id=node_id,
                )
                return task, attempt, result
            except Exception as error:
                last_error = str(error)[:4_000]
                await self.store.save(
                    attempt.model_copy(
                        update={
                            "status": TaskAttemptStatus.FAILED,
                            "failure_reason": last_error,
                            "finished_at": now(),
                        }
                    )
                )
        await self._save_task(
            task,
            status=ProductionTaskStatus.BLOCKED,
            waiting_reasons=[f"执行尝试已达上限：{last_error}"],
        )
        raise DagExecutionError(f"{task.task_id} 执行失败：{last_error}")

    async def _claim_locks(self, tasks: list[ProductionTask]) -> None:
        async with self.resource_guard:
            for task in tasks:
                for lock in task.resource_locks:
                    if any(lock_conflicts(lock, held) for held in self.active_locks):
                        raise DagExecutionError(f"资源锁竞争：{lock}")
                    self.active_locks[lock] = task.task_id

    async def _release_locks(self, tasks: list[ProductionTask]) -> None:
        async with self.resource_guard:
            owners = {item.task_id for item in tasks}
            self.active_locks = {
                lock: owner for lock, owner in self.active_locks.items() if owner not in owners
            }

    async def _integrate_batch(self, plan, results, base_commit: str) -> str:
        integrate = getattr(self.executor, "integrate_batch", None)
        if not integrate:
            return base_commit
        return await integrate(plan, results, base_commit=base_commit)

    async def _current_base(self, plan) -> str:
        current_base = getattr(self.executor, "current_base", None)
        if not current_base:
            return ""
        return await current_base(plan)

    async def _finalize(self, plan, tasks, attempts, base_commit: str) -> ExecutionResult:
        finalize = getattr(self.executor, "finalize", None)
        if finalize:
            return await finalize(plan, tasks, attempts, base_commit=base_commit)
        results = [
            ExecutionResult.model_validate(item.result)
            for item in attempts
            if item.status == TaskAttemptStatus.VALIDATED and item.result
        ]
        return ExecutionResult(
            summary=f"{len(tasks)} 个 DAG 任务已完成",
            evidence="\n".join(item.evidence for item in results)[-50_000:],
            tests=[test for item in results for test in item.tests],
            artifacts=[artifact for item in results for artifact in item.artifacts],
            coding_node=",".join(
                sorted({item.coding_node for item in results if item.coding_node})
            ),
        )

    async def _snapshot(self, plan, tasks, batches, status, reasons=None):
        return await save_snapshot(
            self.store,
            plan,
            tasks,
            batches,
            status,
            self.active_locks,
            reasons,
        )

    async def _plan(self, plan_id, version):
        plan = await self.store.get("execution_plan", plan_id, str(version))
        if not isinstance(plan, ExecutionPlan):
            raise DagExecutionError("执行计划不存在")
        return plan

    async def _tasks(self, plan):
        records = await self.store.list(project_id=plan.project_id, object_type="task")
        return [
            item
            for item in records
            if isinstance(item, ProductionTask)
            and item.plan_id == plan.plan_id
            and item.plan_version == plan.version
        ]

    async def _attempts(self, plan):
        records = await self.store.list(project_id=plan.project_id, object_type="task_attempt")
        return [
            item
            for item in records
            if isinstance(item, TaskAttempt)
            and item.plan_id == plan.plan_id
            and item.plan_version == plan.version
        ]

    async def _batches(self, plan):
        records = await self.store.list(project_id=plan.project_id, object_type="execution_batch")
        return sorted(
            (
                item
                for item in records
                if isinstance(item, ExecutionBatch)
                and item.plan_id == plan.plan_id
                and item.plan_version == plan.version
            ),
            key=lambda item: item.sequence,
        )

    async def _successful_attempt(self, task):
        records = await self.store.list(project_id=task.project_id, object_type="task_attempt")
        matches = [
            item
            for item in records
            if isinstance(item, TaskAttempt)
            and item.task_id == task.task_id
            and item.status in {TaskAttemptStatus.RESULT_RECEIVED, TaskAttemptStatus.VALIDATED}
        ]
        return max(matches, key=lambda item: item.attempt_number) if matches else None

    async def _save_task(self, task, **changes):
        return await self.store.save(task.model_copy(update=changes))
