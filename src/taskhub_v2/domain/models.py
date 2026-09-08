from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Stage(StrEnum):
    INTAKE = "intake"
    PLANNING = "planning"
    PLAN_APPROVAL = "plan_approval"
    IMPLEMENTATION = "implementation"
    IMPLEMENTATION_BLOCKED = "implementation_blocked"
    ACCEPTANCE = "acceptance"
    ACCEPTANCE_BLOCKED = "acceptance_blocked"
    BROWSER_ACCEPTANCE = "browser_acceptance"
    REVIEW = "review"
    RISK = "risk"
    SUPERVISION = "supervision"
    MERGE_APPROVAL = "merge_approval"
    MERGING = "merging"
    MERGE_BLOCKED = "merge_blocked"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


class RunStatus(StrEnum):
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    REJECTED = "rejected"
    BLOCKED = "blocked"
    FAILED = "failed"


class TimelineEvent(BaseModel):
    stage: Stage
    title: str
    detail: str = ""
    actor: str
    status: str = "completed"


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    steps: list[str]
    acceptance: list[str]


T = TypeVar("T")


class ModelResult(BaseModel, Generic[T]):
    content: T
    provider: str
    model: str
    duration_ms: int = 0
    usage: dict[str, Any] = Field(default_factory=dict)
    failed_providers: list[str] = Field(default_factory=list)


class ModelRun(BaseModel):
    role: str
    provider: str
    model: str
    duration_ms: int
    failed_providers: list[str] = Field(default_factory=list)


class Artifact(BaseModel):
    kind: str
    uri: str
    sha256: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProjectDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")
    name: str = Field(default="", min_length=0, max_length=100)
    repository: str
    authority_remote: str = ""
    base_ref: str = "main"
    test_commands: list[list[str]] = Field(default_factory=list)
    acceptance_commands: list[list[str]] = Field(default_factory=list)
    acceptance_capabilities: set[str] = Field(default_factory=set)
    test_timeout_seconds: int = Field(default=600, ge=1, le=3600)
    max_revision_attempts: int = Field(default=2, ge=0, le=10)


class Workspace(BaseModel):
    project_id: str
    path: str
    branch: str
    base_commit: str


class TestExecution(BaseModel):
    command: list[str]
    exit_code: int
    output_tail: str


class NodeDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")
    kind: Literal["local", "remote"]
    url: str = ""
    slots: int = Field(default=1, ge=1, le=16)
    workloads: set[Literal["test", "build", "coding", "acceptance", "browser_acceptance"]] = Field(
        default_factory=lambda: {"test", "build"}
    )
    priority: int = Field(default=100, ge=0, le=10_000)
    enabled: bool = True

    @model_validator(mode="after")
    def remote_requires_url(self):
        if self.kind == "remote" and not self.url.startswith(("http://", "https://")):
            raise ValueError("remote node requires an HTTP URL")
        return self


class ScheduledTests(BaseModel):
    node_id: str
    tests: list[TestExecution]
    artifacts: list[Artifact] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScheduledCoding(BaseModel):
    node_id: str
    result: ModelResult[CodeChangeSummary]


class ExecutionResult(BaseModel):
    summary: str
    evidence: str = ""
    workspace: Workspace | None = None
    commit: str | None = None
    changed_files: list[str] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    tests: list[TestExecution] = Field(default_factory=list)
    execution_node: str = ""
    coding_node: str = ""
    model_run: ModelRun | None = None


class AcceptanceEvidence(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    kind: Literal["test", "database", "browser", "manual", "other"] = "other"
    status: Literal["passed", "failed"]
    source: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=4000)
    tests: list[TestExecution] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)


class AcceptanceResult(BaseModel):
    status: Literal["passed", "failed"]
    evidence: list[AcceptanceEvidence] = Field(default_factory=list)


class AcceptanceSubmission(BaseModel):
    evidence: list[AcceptanceEvidence] = Field(min_length=1, max_length=20)


class PublicationResult(BaseModel):
    project_id: str
    authority_ref: str
    previous_commit: str
    published_commit: str
    branch: str
    rebased: bool = False
    tests: list[TestExecution] = Field(default_factory=list)
    execution_node: str = ""


class CodeChangeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    tests: list[str]


class SupervisionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    summary: str
    reasons: list[str]
    missing_evidence: list[
        Literal["browser", "database", "openapi", "test", "manual"]
    ] = Field(default_factory=list)

    @classmethod
    def response_json_schema(cls) -> dict[str, Any]:
        """Return an OpenAI-strict schema without breaking old checkpoints."""
        schema = cls.model_json_schema()
        schema["required"] = list(schema.get("properties", {}))
        return schema


class StartRunRequest(BaseModel):
    project_id: str = Field(min_length=1, max_length=80)
    requirement: str = Field(min_length=3, max_length=20_000)
    production_line: str = Field(default="default", min_length=1, max_length=80)


class LiteralDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class ApprovalRequest(BaseModel):
    decision: LiteralDecision
    comment: str = Field(default="", max_length=2_000)


class ResumeRequest(BaseModel):
    decision: str = Field(min_length=1, max_length=40)
    comment: str = Field(default="", max_length=2_000)


class RebindProjectRequest(BaseModel):
    project_id: str = Field(min_length=1, max_length=80)


class RunView(BaseModel):
    run_id: str
    project_id: str
    requirement: str
    production_line: str = "default"
    created_at: datetime | None = None
    updated_at: datetime | None = None
    stage: Stage
    status: RunStatus
    next_nodes: list[str]
    pending_action: dict[str, Any] | None = None
    blocking_reason: dict[str, Any] | None = None
    revision_count: int = 0
    max_revision_attempts: int = 2
    revision_feedback: str = ""
    plan: Plan | None = None
    implementation: ExecutionResult | None = None
    acceptance: AcceptanceResult | None = None
    review: str | None = None
    risk: str | None = None
    supervision: SupervisionDecision | None = None
    publication: PublicationResult | None = None
    model_runs: list[ModelRun] = Field(default_factory=list)
    timeline: list[TimelineEvent] = Field(default_factory=list)
    workflow_steps: list[dict[str, str]] = Field(default_factory=list)
    archived_at: datetime | None = None
    original_project_id: str | None = None
    orphaned: bool = False
    project_missing: bool = False
    allowed_actions: list[str] = Field(default_factory=list)


class TaskSummary(BaseModel):
    run_id: str
    requirement_summary: str
    project_id: str
    production_line: str
    stage: Stage
    status: RunStatus
    blocking_reason: dict[str, Any] | None = None
    pending_action: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None
    rebound_project_id: str | None = None
    original_project_id: str | None = None
    orphaned: bool = False
    project_missing: bool = False
    allowed_actions: list[str] = Field(default_factory=list)


class TaskPage(BaseModel):
    items: list[TaskSummary]
    total: int
    page: int
    page_size: int
