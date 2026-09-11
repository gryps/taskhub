from collections import defaultdict, deque


def affected_descendants(tasks, seeds, reasons):
    children = defaultdict(list)
    for task in tasks:
        for dependency in task.depends_on:
            children[dependency].append(task.task_id)
    affected, queue = set(seeds), deque(seeds)
    while queue:
        source = queue.popleft()
        for child in children[source]:
            if child not in affected:
                affected.add(child)
                reasons[child].append(f"depends on affected task {source}")
                queue.append(child)
    return affected


def paths_overlap(left, right):
    left = left.removesuffix("/**").removesuffix("/*").rstrip("/")
    right = right.removesuffix("/**").removesuffix("/*").rstrip("/")
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")
