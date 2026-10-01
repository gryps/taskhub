from collections.abc import Awaitable, Callable

from taskhub_v2.domain.dag import ExecutionBatch, ExecutionBatchStatus
from taskhub_v2.domain.production import (
    ProductionTask,
    ProductionTaskStatus,
    TaskAttemptStatus,
)
from taskhub_v2.services.dag_scheduler_models import now


def failed_gate(error: Exception) -> dict:
    return {"status": "failed", "failed": [{"summary": str(error)[:2_000]}]}


async def persist_gate_failure(
    *,
    store,
    batch: ExecutionBatch,
    completed_results: list[tuple],
    governance_gate: dict,
    save_task: Callable[..., Awaitable[ProductionTask]],
) -> tuple[ExecutionBatch, str]:
    failed_items = governance_gate.get("failed", [])
    detail = "; ".join(
        str(item.get("summary", "治理门禁失败"))
        for item in failed_items[:5]
        if isinstance(item, dict)
    ) or "治理门禁失败"
    reason = "批次全局工程规则门禁未通过：" + detail
    for task, attempt, _result in completed_results:
        await store.save(
            attempt.model_copy(
                update={
                    "status": TaskAttemptStatus.FAILED,
                    "failure_reason": reason,
                    "finished_at": now(),
                }
            )
        )
        await save_task(
            task,
            status=ProductionTaskStatus.BLOCKED,
            waiting_reasons=[reason],
        )
    failed_batch = await store.save(
        batch.model_copy(
            update={
                "status": ExecutionBatchStatus.FAILED,
                "finished_at": now(),
                "governance_gate": governance_gate,
            }
        )
    )
    return failed_batch, reason
