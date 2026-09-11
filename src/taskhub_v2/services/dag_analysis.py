from __future__ import annotations

from collections import Counter, defaultdict, deque
from math import ceil

from taskhub_v2.services.dag_cost import task_cost


def analyze_execution(plan, tasks, batches, attempts) -> dict:
    by_id = {item.task_id: item for item in tasks}
    children = defaultdict(list)
    degree = {}
    for task in tasks:
        dependencies = [item for item in task.depends_on if item in by_id]
        degree[task.task_id] = len(dependencies)
        for dependency in dependencies:
            children[dependency].append(task.task_id)
    queue = deque(sorted(item for item, count in degree.items() if count == 0))
    order = []
    while queue:
        current = queue.popleft()
        order.append(current)
        for child in children[current]:
            degree[child] -= 1
            if degree[child] == 0:
                queue.append(child)

    finish = {}
    predecessor = {}
    level = {}
    for task_id in order:
        task = by_id[task_id]
        parents = [item for item in task.depends_on if item in finish]
        parent = max(parents, key=finish.get) if parents else None
        predecessor[task_id] = parent
        finish[task_id] = (finish[parent] if parent else 0) + task_cost(task)
        level[task_id] = max((level[item] for item in parents), default=-1) + 1
    endpoint = max(order, key=finish.get) if order else None
    critical_path = []
    while endpoint:
        critical_path.append(endpoint)
        endpoint = predecessor[endpoint]
    critical_path.reverse()

    completed = [item for item in tasks if str(item.status) == "completed"]
    remaining = [item for item in tasks if str(item.status) != "completed"]
    budget = int(plan.policy.get("run_cost_budget_units", 100))
    spent = sum(task_cost(item) for item in completed)
    concurrency = int(plan.policy.get("project_concurrency", 2))
    widths = Counter(level.values())
    lock_usage = Counter(lock for item in tasks for lock in item.resource_locks)
    waiting = Counter(reason for item in tasks for reason in item.waiting_reasons)
    bottlenecks = [
        {"type": "resource_lock", "key": key, "task_count": count}
        for key, count in lock_usage.most_common(5)
        if count > 1
    ]
    bottlenecks.extend(
        {"type": "waiting_reason", "key": key, "task_count": count}
        for key, count in waiting.most_common(5)
    )
    validated = [item for item in attempts if str(item.status) == "validated"]
    failed = [item for item in attempts if str(item.status) in {"failed", "timed_out"}]
    reused = [item for item in validated if item.reused_from_attempt_id]
    evidence_complete = [
        item for item in validated if item.evidence_ids or (item.result or {}).get("evidence")
    ]
    max_width = max(widths.values(), default=0)
    return {
        "task_counts": {
            "total": len(tasks),
            "completed": len(completed),
            "remaining": len(remaining),
        },
        "policy": {
            "concurrency_limit": concurrency,
            "priority_weight": int(plan.policy.get("priority_weight", 1)),
            "run_cost_budget_units": budget,
        },
        "prediction": {
            "critical_path_task_ids": critical_path,
            "critical_path_units": finish.get(critical_path[-1], 0) if critical_path else 0,
            "remaining_cost_units": sum(task_cost(item) for item in remaining),
            "estimated_remaining_batches": (
                ceil(len(remaining) / max(1, min(concurrency, max_width or 1)))
            ),
            "max_parallel_width": max_width,
        },
        "cost": {
            "spent_units": spent,
            "remaining_budget_units": max(0, budget - spent),
            "budget_exhausted": spent >= budget,
        },
        "quality": {
            "attempts": len(attempts),
            "failed_attempts": len(failed),
            "reused_validated_attempts": len(reused),
            "evidence_complete_attempts": len(evidence_complete),
            "evidence_completeness": (
                round(len(evidence_complete) / len(validated), 4) if validated else 0.0
            ),
        },
        "bottlenecks": bottlenecks[:10],
        "batch_count": len(batches),
    }
