from taskhub_v2.domain.models import ExecutionResult
from taskhub_v2.domain.production import TaskAttemptStatus


async def current_base(executor, plan) -> str:
    operation = getattr(executor, "current_base", None)
    return await operation(plan) if operation else ""


async def integrate_batch(executor, plan, results, base_commit: str) -> str:
    operation = getattr(executor, "integrate_batch", None)
    return await operation(plan, results, base_commit=base_commit) if operation else base_commit


async def verify_batch(executor, plan, base_commit: str) -> dict:
    operation = getattr(executor, "verify_batch", None)
    return await operation(plan, base_commit=base_commit) if operation else {}


async def finalize(executor, plan, tasks, attempts, base_commit: str) -> ExecutionResult:
    operation = getattr(executor, "finalize", None)
    if operation:
        return await operation(plan, tasks, attempts, base_commit=base_commit)
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
        coding_node=",".join(sorted({item.coding_node for item in results if item.coding_node})),
    )
