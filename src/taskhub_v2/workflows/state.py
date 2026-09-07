from operator import add
from typing import Annotated, Any, TypedDict


class BaseState(TypedDict, total=False):
    run_id: str
    project_id: str
    production_line: str
    requirement: str
    requirement_version: int
    current_stage: str
    status: str
    plan: dict[str, Any] | None
    implementation: str | None
    acceptance: dict[str, Any] | None
    review: str | None
    risk: str | None
    supervision: dict[str, Any] | None
    publication: dict[str, Any] | None
    decision: str | None
    attempt: int
    revision_count: int
    max_revision_attempts: int
    revision_feedback: str
    acceptance_contract_bootstrap_attempted: bool
    pending_action: dict[str, Any] | None
    blocking_reason: dict[str, Any] | None


class CodingState(BaseState):
    timeline: Annotated[list[dict[str, Any]], add]
    model_runs: Annotated[list[dict[str, Any]], add]


class StepState(BaseState):
    """Subgraphs emit only their timeline delta; the parent owns accumulation."""

    timeline: list[dict[str, Any]]
    model_runs: list[dict[str, Any]]


def model_run(role: str, result: Any) -> list[dict[str, Any]]:
    return [
        {
            "role": role,
            "provider": result.provider,
            "model": result.model,
            "duration_ms": result.duration_ms,
            "failed_providers": result.failed_providers,
        }
    ]


def event(stage: str, title: str, actor: str, detail: str = "") -> list[dict[str, str]]:
    return [
        {
            "stage": str(stage),
            "title": title,
            "detail": detail,
            "actor": actor,
            "status": "completed",
        }
    ]
