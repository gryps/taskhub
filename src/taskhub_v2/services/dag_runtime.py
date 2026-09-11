from dataclasses import dataclass

from taskhub_v2.domain.dag import DagExecutionSnapshot, ExecutionBatch
from taskhub_v2.domain.production import ProductionTask, TaskAttempt
from taskhub_v2.services.dag_plan import DagPlanService
from taskhub_v2.services.dag_scheduler import PersistentDagScheduler
from taskhub_v2.workers.dag_executor import WorkerDagExecutor


@dataclass
class ProductizedExecutionRuntime:
    planner: DagPlanService
    scheduler: PersistentDagScheduler
    store: object
    revisions: object | None = None

    async def view(self, project_id: str, run_id: str) -> dict:
        bundle = await self.planner.for_run(project_id, run_id)
        if not bundle:
            return {"execution_plan": None, "tasks": [], "batches": [], "attempts": []}
        records = await self.store.list(project_id=project_id)
        batches = sorted(
            (
                item
                for item in records
                if isinstance(item, ExecutionBatch) and item.plan_id == bundle.plan.plan_id
            ),
            key=lambda item: item.sequence,
        )
        attempts = [
            item
            for item in records
            if isinstance(item, TaskAttempt) and item.plan_id == bundle.plan.plan_id
        ]
        snapshots = sorted(
            (
                item
                for item in records
                if isinstance(item, DagExecutionSnapshot) and item.plan_id == bundle.plan.plan_id
            ),
            key=lambda item: item.sequence,
        )
        tasks = [item for item in bundle.tasks if isinstance(item, ProductionTask)]
        return {
            "execution_plan": bundle.plan,
            "tasks": tasks,
            "batches": batches,
            "attempts": attempts,
            "latest_snapshot": snapshots[-1] if snapshots else None,
        }


def build_dag_runtime(
    store,
    projects,
    worker,
    node_scheduler,
    topology_resolver=None,
    design_contract_resolver=None,
) -> ProductizedExecutionRuntime:
    async def capability_inventory():
        statuses = await node_scheduler.status()
        return {
            capability
            for item in statuses
            if item.get("status") == "ok"
            for capability, available in item.get("capabilities", {}).items()
            if available
        }

    async def capability_resolver(project_id, required, workload):
        statuses = await node_scheduler.status()
        eligible = await topology_resolver(project_id, workload) if topology_resolver else None
        for item in statuses:
            workloads = set(item.get("workloads") or ["test"])
            available = {
                capability
                for capability, enabled in item.get("capabilities", {}).items()
                if enabled
            }
            if (
                item.get("status") == "ok"
                and (eligible is None or item.get("node_id") in eligible)
                and workload in workloads
                and set(required).issubset(available)
            ):
                return True
        return False

    planner = DagPlanService(store, capability_inventory, design_contract_resolver)
    executor = WorkerDagExecutor(
        worker,
        store,
        projects,
        capability_resolver=capability_resolver,
        test_scheduler=node_scheduler,
        topology_resolver=topology_resolver,
    )
    scheduler = PersistentDagScheduler(store, executor)
    return ProductizedExecutionRuntime(planner=planner, scheduler=scheduler, store=store)
