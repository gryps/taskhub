from operator import add
from typing import Annotated, Any, TypedDict


class BaseState(TypedDict, total=False):
    run_id: str
    project_id: str
    production_line: str
    product_spec_id: str | None
    product_spec_version: int | None
    project_contract_id: str | None
    project_contract_version: int | None
    project_contract: dict[str, Any] | None
    requirement: str
    requirement_version: int
    current_stage: str
    status: str
    plan: dict[str, Any] | None
    execution_plan: dict[str, Any] | None
    production_tasks: list[dict[str, Any]]
    execution_batches: list[dict[str, Any]]
    dag_snapshot: dict[str, Any] | None
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
    owner_revision_comment: str
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


def execution_requirement(state: BaseState) -> str:
    requirement = state["requirement"]
    contract = state.get("project_contract")
    if not contract:
        return requirement
    import json

    context = json.dumps(contract, ensure_ascii=False, sort_keys=True)
    return f"{requirement}\n\nApproved ProjectContract (mandatory, machine-readable):\n{context}"
