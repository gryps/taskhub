from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from taskhub_v2.domain.production_base import ProductionRecord


class ExecutionBatchStatus(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ExecutionBatch(ProductionRecord):
    object_type: Literal["execution_batch"] = "execution_batch"
    batch_id: str = Field(pattern=r"^batch_[A-Za-z0-9_-]{3,120}$")
    plan_id: str
    plan_version: int = Field(ge=1)
    sequence: int = Field(ge=1)
    status: ExecutionBatchStatus = ExecutionBatchStatus.PLANNED
    task_ids: list[str] = Field(min_length=1)
    waiting_reasons: dict[str, list[str]] = Field(default_factory=dict)
    assignments: dict[str, str] = Field(default_factory=dict)
    selection_reasons: dict[str, str] = Field(default_factory=dict)
    started_at: str = ""
    finished_at: str = ""


class DagExecutionSnapshot(ProductionRecord):
    object_type: Literal["dag_snapshot"] = "dag_snapshot"
    snapshot_id: str = Field(pattern=r"^snapshot_[A-Za-z0-9_-]{3,120}$")
    plan_id: str
    plan_version: int = Field(ge=1)
    sequence: int = Field(ge=0)
    status: Literal["planning", "running", "waiting", "completed", "blocked"]
    counts: dict[str, int] = Field(default_factory=dict)
    ready_task_ids: list[str] = Field(default_factory=list)
    running_task_ids: list[str] = Field(default_factory=list)
    waiting_reasons: dict[str, list[str]] = Field(default_factory=dict)
    active_locks: dict[str, str] = Field(default_factory=dict)
    node_assignments: dict[str, str] = Field(default_factory=dict)
    detail: dict[str, Any] = Field(default_factory=dict)
