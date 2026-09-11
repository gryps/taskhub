from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class ProductSpecStatus(StrEnum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class ExecutionPlanStatus(StrEnum):
    DRAFT = "draft"
    VALIDATING = "validating"
    ACTIVE = "active"
    COMPLETED = "completed"
    SUPERSEDED = "superseded"
    INVALID = "invalid"


class ProductionTaskStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    ASSIGNED = "assigned"
    RUNNING = "running"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    REVISION_READY = "revision_ready"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


class TaskAttemptStatus(StrEnum):
    CREATED = "created"
    DISPATCHED = "dispatched"
    RUNNING = "running"
    RESULT_RECEIVED = "result_received"
    VALIDATED = "validated"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    ABANDONED = "abandoned"


class ChangeRequestStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    APPLIED = "applied"
    REJECTED = "rejected"


STATE_TRANSITIONS: dict[str, dict[str, set[str]]] = {
    "product_spec": {
        "draft": {"in_review", "rejected"},
        "in_review": {"approved", "rejected", "draft"},
        "approved": {"superseded"},
    },
    "execution_plan": {
        "draft": {"validating"},
        "validating": {"active", "invalid", "draft"},
        "active": {"completed", "superseded"},
    },
    "task": {
        "pending": {"ready", "blocked", "cancelled", "superseded"},
        "ready": {"assigned", "blocked", "cancelled", "superseded"},
        "assigned": {"running", "ready", "blocked", "cancelled"},
        "running": {"verifying", "blocked", "revision_ready", "cancelled"},
        "verifying": {"completed", "blocked", "revision_ready"},
        "blocked": {"ready", "revision_ready", "cancelled", "superseded"},
        "revision_ready": {"ready", "cancelled", "superseded"},
    },
    "task_attempt": {
        "created": {"dispatched", "abandoned"},
        "dispatched": {"running", "failed", "timed_out", "abandoned"},
        "running": {"result_received", "failed", "timed_out", "abandoned"},
        "result_received": {"validated", "failed"},
    },
    "change_request": {
        "proposed": {"approved", "rejected"},
        "approved": {"applied", "rejected"},
    },
    "capability_pack": {
        "draft": {"trusted", "rejected"},
        "trusted": {"disabled"},
        "disabled": {"trusted"},
    },
}


def validate_transition(object_type: str, previous: str, target: str) -> None:
    if previous == target:
        return
    if target not in STATE_TRANSITIONS.get(object_type, {}).get(previous, set()):
        raise ValueError(f"illegal {object_type} state transition: {previous} -> {target}")


class ProductionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1, max_length=80)
    created_by: str = Field(default="system", min_length=1, max_length=100)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    source_ids: list[str] = Field(default_factory=list)
    content_digest: str = Field(default="", max_length=128)


class ProductSpec(ProductionRecord):
    object_type: Literal["product_spec"] = "product_spec"
    spec_id: str = Field(pattern=r"^ps_[A-Za-z0-9_-]{3,100}$")
    version: int = Field(ge=1)
    status: ProductSpecStatus = ProductSpecStatus.DRAFT
    previous_version: int | None = Field(default=None, ge=1)
    change_request_id: str | None = None
    goals: list[str] = Field(default_factory=list)
    personas: list[str] = Field(default_factory=list)
    in_scope: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    functional_requirements: list[str] = Field(default_factory=list)
    non_functional_requirements: list[str] = Field(default_factory=list)
    modules: list[str] = Field(default_factory=list)
    interfaces: list[dict[str, Any]] = Field(default_factory=list)
    data_entities: list[dict[str, Any]] = Field(default_factory=list)
    security_requirements: list[str] = Field(default_factory=list)
    delivery_requirements: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    capability_pack_lock: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def version_chain_is_explicit(self):
        if self.version == 1 and self.previous_version is not None:
            raise ValueError("first ProductSpec version cannot reference a previous version")
        if self.version > 1 and self.previous_version != self.version - 1:
            raise ValueError("new ProductSpec version must reference its immediate predecessor")
        return self


class ExecutionPlan(ProductionRecord):
    object_type: Literal["execution_plan"] = "execution_plan"
    plan_id: str = Field(pattern=r"^plan_[A-Za-z0-9_-]{3,100}$")
    version: int = Field(ge=1)
    status: ExecutionPlanStatus = ExecutionPlanStatus.DRAFT
    product_spec_id: str
    product_spec_version: int = Field(ge=1)
    task_ids: list[str] = Field(default_factory=list)
    milestones: list[dict[str, Any]] = Field(default_factory=list)
    policy: dict[str, Any] = Field(default_factory=dict)
    legacy_run_id: str | None = None


class ProductionTask(ProductionRecord):
    object_type: Literal["task"] = "task"
    task_id: str = Field(pattern=r"^task_[A-Za-z0-9_-]{3,100}$")
    plan_id: str
    plan_version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=4_000)
    status: ProductionTaskStatus = ProductionTaskStatus.PENDING
    depends_on: list[str] = Field(default_factory=list)
    inputs: list[dict[str, Any]] = Field(default_factory=list)
    outputs: list[dict[str, Any]] = Field(default_factory=list)
    allowed_paths: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    contracts: list[str] = Field(default_factory=list)
    acceptance_commands: list[list[str]] = Field(default_factory=list)
    expected_artifacts: list[str] = Field(default_factory=list)
    estimated_size: Literal["small", "medium", "large"] = "small"
    risk_level: Literal["low", "medium", "high"] = "medium"

    @model_validator(mode="after")
    def dependencies_do_not_reference_self(self):
        if self.task_id in self.depends_on:
            raise ValueError("task cannot depend on itself")
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError("task dependencies must be unique")
        return self


class TaskAttempt(ProductionRecord):
    object_type: Literal["task_attempt"] = "task_attempt"
    attempt_id: str = Field(pattern=r"^attempt_[A-Za-z0-9_-]{3,100}$")
    task_id: str
    attempt_number: int = Field(ge=1)
    status: TaskAttemptStatus = TaskAttemptStatus.CREATED
    node_id: str = ""
    workspace_digest: str = ""
    request_digest: str = ""
    result: dict[str, Any] | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class ChangeRequest(ProductionRecord):
    object_type: Literal["change_request"] = "change_request"
    change_request_id: str = Field(pattern=r"^cr_[A-Za-z0-9_-]{3,100}$")
    version: int = Field(default=1, ge=1)
    status: ChangeRequestStatus = ChangeRequestStatus.PROPOSED
    reason: str = Field(min_length=1, max_length=4_000)
    source_event: str = Field(min_length=1, max_length=200)
    affected_task_ids: list[str] = Field(default_factory=list)
    superseded_task_ids: list[str] = Field(default_factory=list)
    added_task_ids: list[str] = Field(default_factory=list)
    regression_scope: list[str] = Field(default_factory=list)
    plan_diff: dict[str, Any] = Field(default_factory=dict)


class CapabilityPack(ProductionRecord):
    object_type: Literal["capability_pack"] = "capability_pack"
    pack_id: str = Field(pattern=r"^pack_[A-Za-z0-9_-]{3,100}$")
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?$")
    status: Literal["draft", "trusted", "disabled", "rejected"] = "draft"
    pack_type: Literal[
        "frontend-style",
        "frontend-components",
        "frontend-layout",
        "brand",
        "project-architecture",
        "testing",
        "security",
        "delivery",
    ]
    source: str
    summary: str
    compatibility: dict[str, Any] = Field(default_factory=dict)
    files: list[str] = Field(default_factory=list)
    validators: list[str] = Field(default_factory=list)
    license: str = ""
    contains_executable: bool = False
    required_permissions: list[str] = Field(default_factory=list)


ProductionObject = Annotated[
    ProductSpec | ExecutionPlan | ProductionTask | TaskAttempt | ChangeRequest | CapabilityPack,
    Field(discriminator="object_type"),
]
production_object_adapter = TypeAdapter(ProductionObject)


def object_identity(record: ProductionObject) -> tuple[str, str, str]:
    match record:
        case ProductSpec():
            identity = record.spec_id, str(record.version)
        case ExecutionPlan():
            identity = record.plan_id, str(record.version)
        case ProductionTask():
            identity = record.task_id, "1"
        case TaskAttempt():
            identity = record.attempt_id, str(record.attempt_number)
        case ChangeRequest():
            identity = record.change_request_id, str(record.version)
        case CapabilityPack():
            identity = record.pack_id, record.version
    return record.object_type, *identity


def immutable_content(record: ProductionObject) -> dict[str, Any]:
    return record.model_dump(
        mode="json",
        exclude={"status", "created_at", "updated_at", "created_by", "content_digest"},
    )


def with_content_digest(record: ProductionObject) -> ProductionObject:
    encoded = json.dumps(
        immutable_content(record), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return record.model_copy(update={"content_digest": sha256(encoded).hexdigest()})
