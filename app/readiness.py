from __future__ import annotations

from collections import Counter, deque
from typing import Any


IMPLEMENTATION_WORKERS = {"worker-31-31-implementation-a", "worker-31-31-implementation"}


def task_type_set(worker: dict[str, Any]) -> set[str]:
    return {item.strip() for item in str(worker.get("task_types") or "").split(",") if item.strip()}


def assess_workers(workers: list[dict[str, Any]]) -> dict[str, Any]:
    online = {item.get("worker_id"): item for item in workers if item.get("status") == "ok"}
    implementation = sorted(IMPLEMENTATION_WORKERS & set(online))
    quality = online.get("worker-31-24-quality")
    return {
        "dual_pipeline_ready": len(implementation) == 2 and all(
            "code.change" in task_type_set(online[worker_id]) for worker_id in implementation
        ),
        "implementation_workers": implementation,
        "quality_ready": bool(quality and "test.run" in task_type_set(quality)),
    }


def simulate_dual_pipeline(task_count: int = 100, fail_after: int | None = None) -> dict[str, Any]:
    count = min(10000, max(2, task_count))
    queues = {
        "pipeline-a": deque(range(0, count, 2)),
        "pipeline-b": deque(range(1, count, 2)),
    }
    workers = {
        "worker-31-31-implementation-a": "pipeline-a",
        "worker-31-31-implementation": "pipeline-b",
    }
    completed: list[tuple[str, str, int]] = []
    ticks = 0
    failed_worker = "worker-31-31-implementation" if fail_after is not None else None
    while any(queues.values()):
        ticks += 1
        progressed = False
        for worker, pipeline in workers.items():
            if worker == failed_worker and ticks > int(fail_after or 0):
                continue
            if queues[pipeline]:
                completed.append((worker, pipeline, queues[pipeline].popleft()))
                progressed = True
        if not progressed:
            break
    violations = [item for item in completed if workers[item[0]] != item[1]]
    remaining = sum(len(queue) for queue in queues.values())
    return {
        "tasks": count,
        "completed": len(completed),
        "remaining": remaining,
        "ticks": ticks,
        "pipeline_isolation_violations": len(violations),
        "completed_by_pipeline": dict(Counter(item[1] for item in completed)),
        "failed_worker": failed_worker,
        "passed": not violations and (remaining == 0 if fail_after is None else remaining > 0),
    }
