COST_UNITS = {"small": 1, "medium": 3, "large": 8}


def task_cost(task) -> int:
    return COST_UNITS.get(task.estimated_size, 3)


def apply_cost_budget(tasks, selected, reasons, budget):
    spent = sum(task_cost(item) for item in tasks if str(item.status) == "completed")
    remaining = max(0, budget - spent)
    affordable = []
    for task in selected:
        cost = task_cost(task)
        if cost <= remaining:
            affordable.append(task)
            remaining -= cost
        else:
            reasons[task.task_id].append(
                f"等待运行成本预算：任务需要 {cost}，剩余 {remaining} 单位"
            )
    return affordable, spent, remaining
