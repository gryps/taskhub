from taskhub_v2.domain.production import ProductionTaskStatus
from taskhub_v2.services.dag_scheduler_models import lock_conflicts


async def waiting_reasons(plan, tasks, completed, active_locks, capability_reason=None):
    reasons = {}
    for task in tasks:
        current = []
        missing = [item for item in task.depends_on if item not in completed]
        if missing:
            current.append("等待依赖：" + ", ".join(missing))
        if not task.input_contracts_frozen:
            current.append("输入合同尚未冻结")
        if plan.policy.get("governance_pending"):
            current.append("等待人工治理决定")
        conflicting_locks = [
            lock
            for lock in task.resource_locks
            if any(lock_conflicts(lock, held) for held in active_locks)
        ]
        if conflicting_locks:
            current.append("等待资源锁：" + ", ".join(conflicting_locks))
        if task.status == ProductionTaskStatus.BLOCKED:
            current.extend(task.waiting_reasons or ["任务已阻塞"])
        if capability_reason:
            reason = await capability_reason(task)
            if reason:
                current.append(reason)
        reasons[task.task_id] = current
    return reasons


def select_non_conflicting(candidates, limit, active_locks):
    selected = []
    locks = set(active_locks)
    for task in candidates:
        if len(selected) >= limit:
            break
        if any(lock_conflicts(lock, held) for lock in task.resource_locks for held in locks):
            continue
        selected.append(task)
        locks.update(task.resource_locks)
    return selected


def add_selection_waiting_reasons(candidates, selected, reasons):
    selected_ids = {item.task_id for item in selected}
    selected_locks = {lock for item in selected for lock in item.resource_locks}
    for task in candidates:
        if task.task_id in selected_ids:
            continue
        conflicts = [
            lock
            for lock in task.resource_locks
            if any(lock_conflicts(lock, held) for held in selected_locks)
        ]
        reasons[task.task_id].append(
            "等待资源锁：" + ", ".join(conflicts) if conflicts else "等待项目并发预算"
        )
