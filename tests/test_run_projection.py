import asyncio

from taskhub_v2.domain.dag import DagExecutionSnapshot, ExecutionBatch
from taskhub_v2.domain.models import RunView
from taskhub_v2.domain.production import ExecutionPlan, ProductionTask
from taskhub_v2.services.run_projection import with_live_execution


def test_live_execution_replaces_stale_checkpoint_dag_fields():
    plan = ExecutionPlan(
        project_id="demo",
        plan_id="plan_live",
        version=1,
        status="active",
        product_spec_id="ps_demo",
        product_spec_version=1,
        run_id="run-live",
        task_ids=["task_live_one"],
    )
    task = ProductionTask(
        project_id="demo",
        task_id="task_live_one",
        plan_id=plan.plan_id,
        plan_version=1,
        title="live",
        objective="live",
        status="running",
        acceptance_commands=[["test"]],
    )
    batch = ExecutionBatch(
        project_id="demo",
        batch_id="batch_live_001",
        plan_id=plan.plan_id,
        plan_version=1,
        sequence=1,
        status="running",
        task_ids=[task.task_id],
    )
    snapshot = DagExecutionSnapshot(
        project_id="demo",
        snapshot_id="snapshot_live",
        plan_id=plan.plan_id,
        plan_version=1,
        sequence=1,
        status="running",
        counts={"running": 1},
    )

    class Runtime:
        async def view(self, project_id, run_id):
            assert (project_id, run_id) == ("demo", "run-live")
            return {
                "execution_plan": plan,
                "tasks": [task],
                "batches": [batch],
                "attempts": [],
                "latest_snapshot": snapshot,
            }

    stale = RunView(
        run_id="run-live",
        project_id="demo",
        requirement="live status",
        stage="implementation",
        status="running",
        next_nodes=[],
        production_tasks=[],
    )
    projected = asyncio.run(with_live_execution(stale, Runtime()))

    assert projected.production_tasks[0]["status"] == "running"
    assert projected.execution_batches[0]["batch_id"] == "batch_live_001"
    assert projected.dag_snapshot["counts"] == {"running": 1}
