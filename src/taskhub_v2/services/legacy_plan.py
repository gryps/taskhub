from __future__ import annotations

from hashlib import sha256

from pydantic import BaseModel

from taskhub_v2.domain.models import RunStatus, RunView
from taskhub_v2.domain.production import (
    ExecutionPlan,
    ExecutionPlanStatus,
    ProductionTask,
    ProductionTaskStatus,
    ProductSpec,
    ProductSpecStatus,
)


class LegacyExecutionBatch(BaseModel):
    batch_id: str
    plan_id: str
    task_ids: list[str]
    dynamic: bool = True
    compatibility_mode: str = "legacy-linear"


class LegacyLinearPlanView(BaseModel):
    product_spec: ProductSpec
    execution_plan: ExecutionPlan
    task: ProductionTask
    batch: LegacyExecutionBatch


def legacy_linear_plan(run: RunView) -> LegacyLinearPlanView:
    """Project a legacy LangGraph run without inventing detailed DAG evidence."""
    suffix = sha256(run.run_id.encode()).hexdigest()[:20]
    spec_id = f"ps_legacy_{suffix}"
    plan_id = f"plan_legacy_{suffix}"
    task_id = f"task_legacy_{suffix}"
    completed = run.status == RunStatus.COMPLETED
    plan_status = ExecutionPlanStatus.COMPLETED if completed else ExecutionPlanStatus.ACTIVE
    task_status = {
        RunStatus.COMPLETED: ProductionTaskStatus.COMPLETED,
        RunStatus.BLOCKED: ProductionTaskStatus.BLOCKED,
        RunStatus.REJECTED: ProductionTaskStatus.CANCELLED,
        RunStatus.FAILED: ProductionTaskStatus.BLOCKED,
        RunStatus.WAITING: ProductionTaskStatus.BLOCKED,
        RunStatus.RUNNING: ProductionTaskStatus.RUNNING,
    }[run.status]
    created_at = run.created_at or run.updated_at
    common = {
        "project_id": run.project_id,
        "created_by": "legacy-migration",
        "source_ids": [run.run_id],
    }
    if created_at:
        common["created_at"] = created_at
    if run.updated_at:
        common["updated_at"] = run.updated_at
    spec = ProductSpec(
        **common,
        spec_id=spec_id,
        version=1,
        status=ProductSpecStatus.APPROVED,
        goals=[run.requirement],
        in_scope=["Legacy linear workflow result"],
        acceptance_criteria=list(run.plan.acceptance) if run.plan else [],
        assumptions=["Mapped from an existing run; no detailed DAG evidence was inferred."],
    )
    plan = ExecutionPlan(
        **common,
        plan_id=plan_id,
        version=1,
        status=plan_status,
        product_spec_id=spec_id,
        product_spec_version=1,
        task_ids=[task_id],
        policy={"production_line": run.production_line or "default"},
        legacy_run_id=run.run_id,
    )
    task = ProductionTask(
        **common,
        task_id=task_id,
        plan_id=plan_id,
        plan_version=1,
        title=(run.requirement[:197] + "...") if len(run.requirement) > 200 else run.requirement,
        objective=run.requirement,
        status=task_status,
        acceptance_commands=[],
        expected_artifacts=[],
    )
    return LegacyLinearPlanView(
        product_spec=spec,
        execution_plan=plan,
        task=task,
        batch=LegacyExecutionBatch(
            batch_id=f"batch_legacy_{suffix}", plan_id=plan_id, task_ids=[task_id]
        ),
    )
