import hashlib
import json
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, Field

from taskhub_v2.domain.dag import DagExecutionSnapshot, ExecutionBatch
from taskhub_v2.domain.models import ExecutionResult
from taskhub_v2.domain.production import ExecutionPlan, ProductionTask, TaskAttempt


def now() -> str:
    return datetime.now(UTC).isoformat()


def idempotency_key(task: ProductionTask, base_commit: str) -> str:
    payload = json.dumps(
        {"task": task.model_dump(mode="json"), "base_commit": base_commit},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def lock_conflicts(left: str, right: str) -> bool:
    if left == right:
        return True
    if left.startswith("path:") and right.startswith("path:"):
        left_path, right_path = left[5:].rstrip("/"), right[5:].rstrip("/")
        return left_path.startswith(right_path + "/") or right_path.startswith(left_path + "/")
    return False


class DagTaskExecutor(Protocol):
    async def execute(
        self, task: ProductionTask, attempt: TaskAttempt, *, base_commit: str
    ) -> ExecutionResult: ...


class DagExecutionError(RuntimeError):
    def __init__(self, detail: str, snapshot: DagExecutionSnapshot | None = None):
        self.detail = detail
        self.snapshot = snapshot
        super().__init__(detail)


class DagExecutionOutcome(BaseModel):
    plan: ExecutionPlan
    tasks: list[ProductionTask]
    batches: list[ExecutionBatch] = Field(default_factory=list)
    attempts: list[TaskAttempt] = Field(default_factory=list)
    snapshot: DagExecutionSnapshot
    implementation: ExecutionResult
