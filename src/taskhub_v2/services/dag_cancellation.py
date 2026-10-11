from taskhub_v2.domain.dag import ExecutionBatch, ExecutionBatchStatus
from taskhub_v2.domain.production import (
    ExecutionPlanStatus,
    ProductionTaskStatus,
    TaskAttempt,
    TaskAttemptStatus,
)
from taskhub_v2.services.dag_scheduler_models import now
from taskhub_v2.services.dag_scheduler_state import save_snapshot


async def cancel_execution(store, bundle, records) -> None:
    tasks = await _cancel_tasks(store, bundle.tasks)
    attempts = [
        item
        for item in records
        if isinstance(item, TaskAttempt) and item.plan_id == bundle.plan.plan_id
    ]
    batches = [
        item
        for item in records
        if isinstance(item, ExecutionBatch) and item.plan_id == bundle.plan.plan_id
    ]
    await _abandon_attempts(store, attempts)
    await _fail_batches(store, batches)
    plan = bundle.plan
    if plan.status == ExecutionPlanStatus.ACTIVE:
        plan = await store.save(
            plan.model_copy(update={"status": ExecutionPlanStatus.SUPERSEDED})
        )
    await save_snapshot(
        store,
        plan,
        tasks,
        batches,
        "blocked",
        {},
        {task.task_id: task.waiting_reasons for task in tasks if task.waiting_reasons},
    )


async def _cancel_tasks(store, source):
    tasks = []
    terminal = {
        ProductionTaskStatus.COMPLETED,
        ProductionTaskStatus.CANCELLED,
        ProductionTaskStatus.SUPERSEDED,
    }
    for task in source:
        if task.status not in terminal:
            task = await store.save(
                task.model_copy(
                    update={
                        "status": ProductionTaskStatus.CANCELLED,
                        "waiting_reasons": ["运行已由操作者归档"],
                    }
                )
            )
        tasks.append(task)
    return tasks


async def _abandon_attempts(store, attempts) -> None:
    active = {
        TaskAttemptStatus.CREATED,
        TaskAttemptStatus.DISPATCHED,
        TaskAttemptStatus.RUNNING,
        TaskAttemptStatus.RESULT_RECEIVED,
    }
    for attempt in attempts:
        if attempt.status in active:
            await store.save(
                attempt.model_copy(
                    update={
                        "status": TaskAttemptStatus.ABANDONED,
                        "failure_reason": "运行已由操作者归档",
                        "finished_at": now(),
                    }
                )
            )


async def _fail_batches(store, batches) -> None:
    for batch in batches:
        if batch.status in {ExecutionBatchStatus.PLANNED, ExecutionBatchStatus.RUNNING}:
            await store.save(
                batch.model_copy(
                    update={
                        "status": ExecutionBatchStatus.FAILED,
                        "finished_at": now(),
                    }
                )
            )
