from collections import Counter

from taskhub_v2.domain.dag import DagExecutionSnapshot
from taskhub_v2.domain.production import ProductionTaskStatus


async def save_snapshot(store, plan, tasks, batches, status, active_locks, reasons=None):
    existing = await store.list(project_id=plan.project_id, object_type="dag_snapshot")
    sequence = 1 + max(
        (item.sequence for item in existing if item.plan_id == plan.plan_id), default=-1
    )
    snapshot = DagExecutionSnapshot(
        project_id=plan.project_id,
        snapshot_id=f"snapshot_{plan.plan_id.removeprefix('plan_')}",
        plan_id=plan.plan_id,
        plan_version=plan.version,
        sequence=sequence,
        status=status,
        counts=dict(Counter(str(item.status) for item in tasks)),
        ready_task_ids=[
            item.task_id for item in tasks if item.status == ProductionTaskStatus.READY
        ],
        running_task_ids=[
            item.task_id for item in tasks if item.status == ProductionTaskStatus.RUNNING
        ],
        waiting_reasons=reasons
        or {item.task_id: item.waiting_reasons for item in tasks if item.waiting_reasons},
        active_locks=dict(active_locks),
        node_assignments={
            item.task_id: item.assigned_node_id for item in tasks if item.assigned_node_id
        },
        detail={"batch_ids": [item.batch_id for item in batches]},
        created_by="scheduler",
    )
    return await store.save(snapshot)
