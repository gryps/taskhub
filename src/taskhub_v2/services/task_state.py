"""Project checkpoint state into task-center metadata without changing the workflow."""
from taskhub_v2.domain.models import ExecutionResult, RunStatus

NODE_STAGES = {
    "acceptance": "acceptance", "acceptance_recovery": "acceptance_blocked",
    "supervisor": "supervision", "revision": "implementation",
    "supervision_recovery": "supervision",
    "acceptance_revision": "implementation",
    "revision_limit": "supervision", "publication": "merging",
    "publication_recovery": "merge_blocked",
    "implementation_recovery": "implementation_blocked",
}


def checkpoint_values(snapshot):
    values = dict(snapshot.values)
    implementation = values.get("implementation")
    if implementation is not None and not isinstance(implementation, dict | ExecutionResult):
        values["implementation"] = ExecutionResult(summary=str(implementation))
    errors = [task.error for task in snapshot.tasks if task.error]
    if errors:
        values.update(status="failed", pending_action=None,
                      blocking_reason={"code": "workflow_error", "detail": str(errors[0])})
    if snapshot.next and values.get("status") in {"running", "failed"}:
        node = snapshot.next[0]
        values["current_stage"] = NODE_STAGES.get(node, node)
    return values


def workflow_steps(values):
    steps = [
        ("intake", "需求"), ("planning", "规划"), ("plan_approval", "计划审批"),
        ("implementation", "实施"), ("acceptance", "验收"),
        ("review", "审查"), ("risk", "风险"),
        ("supervision", "监督"), ("merge_approval", "发布审批"),
        ("merging", "发布"), ("completed", "完成"),
    ]
    aliases = {
        "implementation_blocked": "implementation",
        "acceptance_blocked": "acceptance",
        "merge_blocked": "merging",
    }
    stage, status = values["current_stage"], values["status"]
    active_id = aliases.get(stage, stage)
    ids = [step[0] for step in steps]
    if active_id not in ids:
        active_id = next((aliases.get(e["stage"], e["stage"])
                          for e in reversed(values.get("timeline", []))
                          if aliases.get(e["stage"], e["stage"]) in ids), None)
    active = ids.index(active_id) if active_id in ids else -1
    result = []
    for index, (step_id, label) in enumerate(steps):
        state = "completed" if index < active else "not_started"
        if status == RunStatus.COMPLETED:
            state = "completed"
        elif index == active:
            state = ("blocked" if status in {"blocked", "failed", "rejected"}
                     else "waiting_manual" if status == "waiting" else "current")
        result.append({"id": step_id, "label": label, "state": state})
    return result
