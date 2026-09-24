from langgraph.types import interrupt

from taskhub_v2.domain.models import RunStatus, Stage
from taskhub_v2.workflows.state import CodingState, event


async def recover_publication(state: CodingState) -> dict:
    response = interrupt(
        {
            "type": "publication_recovery",
            "run_id": state["run_id"],
            "reason": state.get("blocking_reason"),
            "choices": ["retry", "revise", "cancel"],
        }
    )
    decision = response.get("decision") if isinstance(response, dict) else response
    retry = decision == "retry"
    revise = decision == "revise"
    reason = state.get("blocking_reason") or {}
    comment = response.get("comment", "").strip() if isinstance(response, dict) else ""
    revision_comment = ""
    if revise:
        revision_comment = (
            "Publication recovery required after authority advancement. "
            f"{reason.get('code', 'publication_failed')}: "
            f"{reason.get('detail', 'publication could not complete')}."
        )
        if comment:
            revision_comment += "\n" + comment
    return {
        "decision": decision,
        "owner_revision_comment": revision_comment,
        "pending_action": None,
        "blocking_reason": None if retry or revise else state.get("blocking_reason"),
        "current_stage": (
            Stage.MERGING.value
            if retry
            else Stage.IMPLEMENTATION.value
            if revise
            else Stage.REJECTED.value
        ),
        "status": RunStatus.RUNNING.value if retry or revise else RunStatus.REJECTED.value,
        "timeline": event(
            Stage.MERGE_BLOCKED,
            (
                "Publication retry requested"
                if retry
                else "Publication conflict returned to implementation"
                if revise
                else "Run cancelled"
            ),
            "owner",
            comment,
        ),
    }


def route_publication_recovery(state: CodingState) -> str:
    decision = state.get("decision")
    if decision == "retry":
        return "publication"
    if decision == "revise":
        return "revision"
    return "reject"
