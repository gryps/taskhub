from pathlib import PurePosixPath

from pydantic import BaseModel

from taskhub_v2.domain.production import ProductionTask
from taskhub_v2.domain.project_contract import ProjectContract


class DagValidationFinding(BaseModel):
    code: str
    task_id: str = ""
    detail: str


def lock_path(path: str) -> str:
    return path.split("*")[0].rstrip("/")


def paths_overlap(left: str, right: str) -> bool:
    left_path = PurePosixPath(lock_path(left))
    right_path = PurePosixPath(lock_path(right))
    return (
        left_path == right_path
        or left_path in right_path.parents
        or right_path in left_path.parents
    )


def contract_findings(
    task: ProductionTask, contract: ProjectContract
) -> list[DagValidationFinding]:
    allowed_roots = [lock_path(path) for module in contract.modules for path in module.paths]
    return [
        DagValidationFinding(
            code="contract_path_violation",
            task_id=task.task_id,
            detail=f"{task.task_id} 的修改范围 {path} 不属于已批准项目契约",
        )
        for path in task.allowed_paths
        if not any(paths_overlap(path, root) for root in allowed_roots)
    ]


def interface_findings(tasks: list[ProductionTask]) -> list[DagValidationFinding]:
    findings = []
    for index, left in enumerate(tasks):
        for right in tasks[index + 1 :]:
            if left.task_id in right.depends_on or right.task_id in left.depends_on:
                continue
            overlaps = [
                path
                for path in left.allowed_paths
                if any(paths_overlap(path, other) for other in right.allowed_paths)
            ]
            if overlaps and not set(left.resource_locks) & set(right.resource_locks):
                findings.append(
                    DagValidationFinding(
                        code="unsafe_parallel_overlap",
                        task_id=right.task_id,
                        detail=f"{left.task_id} 与 {right.task_id} 修改范围重叠却没有共享锁",
                    )
                )
    return findings


def dataflow_findings(tasks: list[ProductionTask]) -> list[DagValidationFinding]:
    by_id = {item.task_id: item for item in tasks}
    consumed = {
        item.get("source_output", "")
        for task in tasks
        for item in task.inputs
        if item.get("source_output")
    }
    findings = []
    for task in tasks:
        for item in task.inputs:
            source_id = item.get("source_task_id", "")
            source_output = item.get("source_output", "")
            source = by_id.get(source_id)
            available = {output.get("id", "") for output in source.outputs} if source else set()
            if source_id not in task.depends_on or source_output not in available:
                findings.append(
                    DagValidationFinding(
                        code="input_source_missing",
                        task_id=task.task_id,
                        detail=f"{task.task_id} 的输入 {source_output or source_id} 没有有效来源",
                    )
                )
        for output in task.outputs:
            output_id = output.get("id", "")
            if output.get("kind") != "deliverable" and output_id not in consumed:
                findings.append(
                    DagValidationFinding(
                        code="output_unconsumed",
                        task_id=task.task_id,
                        detail=f"{task.task_id} 的输出 {output_id} 没有消费者",
                    )
                )
    return findings
