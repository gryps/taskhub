from langgraph.types import Command


class RunActionConflict(ValueError):
    pass


def build_resume_command(current, request) -> Command:
    if current.archived_at:
        raise RunActionConflict("task is archived; workflow actions are disabled")
    if request.decision in {"retry", "approve", "reassess"} and current.project_missing:
        raise RunActionConflict(
            "project is not registered; archive the task or rebind it to an existing project"
        )
    action = current.pending_action or {}
    choices = list(action.get("choices", []))
    # Older durable checkpoints can use recovery choices introduced after they were created.
    if action.get("type") == "acceptance_recovery" and "revise" not in choices:
        choices.append("revise")
    if action.get("type") == "revision_limit" and "manual" not in choices:
        choices.append("manual")
    if (
        action.get("type") == "revision_limit"
        and current.supervision
        and current.supervision.missing_evidence
        and "recheck" not in choices
    ):
        choices.append("recheck")
    if not current.next_nodes or request.decision not in choices:
        raise RunActionConflict("decision is not valid for the pending action")
    update = {"project_id": current.project_id} if current.original_project_id else None
    return Command(resume=request.model_dump(mode="json"), update=update)
