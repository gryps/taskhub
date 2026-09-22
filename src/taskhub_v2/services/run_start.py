from typing import Any
from uuid import uuid4

from taskhub_v2.domain.models import RunStatus, Stage, StartRunRequest


def build_start_payload(
    projects, request: StartRunRequest, project_contract: dict[str, Any] | None
) -> tuple[str, dict[str, Any]]:
    max_revisions = (
        projects.get(request.project_id).max_revision_attempts if projects is not None else 2
    )
    run_id = str(uuid4())
    return run_id, {
        "run_id": run_id,
        "project_id": request.project_id,
        "production_line": request.production_line,
        "product_spec_id": request.product_spec_id,
        "product_spec_version": request.product_spec_version,
        "project_contract_id": request.project_contract_id,
        "project_contract_version": request.project_contract_version,
        "project_contract": project_contract,
        "requirement": request.requirement,
        "requirement_version": 1,
        "current_stage": Stage.INTAKE.value,
        "status": RunStatus.RUNNING.value,
        "plan": None,
        "execution_plan": None,
        "production_tasks": [],
        "execution_batches": [],
        "dag_snapshot": None,
        "implementation": None,
        "acceptance": None,
        "review": None,
        "risk": None,
        "supervision": None,
        "publication": None,
        "decision": None,
        "attempt": 0,
        "revision_count": 0,
        "max_revision_attempts": max_revisions,
        "revision_feedback": "",
        "acceptance_contract_bootstrap_attempted": False,
        "pending_action": None,
        "blocking_reason": None,
        "model_runs": [],
        "timeline": [],
    }
