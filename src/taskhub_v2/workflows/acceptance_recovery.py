from langgraph.types import interrupt

from taskhub_v2.domain.models import RunStatus, Stage
from taskhub_v2.workflows.state import CodingState, event


async def recover_acceptance(state: CodingState) -> dict:
    response = interrupt(
        {
            "type": "acceptance_recovery",
            "run_id": state["run_id"],
            "reason": state.get("blocking_reason"),
            "choices": ["retry", "revise", "cancel"],
        }
    )
    decision = response.get("decision") if isinstance(response, dict) else response
    retry = decision == "retry"
    revise = decision == "revise"
    return {
        "decision": decision,
        "pending_action": None,
        # A coding revision needs the original acceptance diagnostic as feedback.
        "blocking_reason": None if retry else state.get("blocking_reason"),
        "current_stage": (
            Stage.ACCEPTANCE.value
            if retry
            else Stage.IMPLEMENTATION.value
            if revise
            else Stage.REJECTED.value
        ),
        "status": RunStatus.RUNNING.value if retry or revise else RunStatus.REJECTED.value,
        "timeline": event(
            Stage.ACCEPTANCE_BLOCKED,
            (
                "Acceptance retry requested"
                if retry
                else "Acceptance returned to implementation"
                if revise
                else "Run cancelled"
            ),
            "owner",
            response.get("comment", "") if isinstance(response, dict) else "",
        ),
    }


async def prepare_acceptance_revision(state: CodingState) -> dict:
    reason = state.get("blocking_reason") or {}
    feedback = "Acceptance failed"
    if reason:
        feedback = f"{reason.get('code', 'acceptance_failed')}: {reason.get('detail', '')}"
    revision = int(state.get("revision_count", 0)) + 1
    maximum = max(int(state.get("max_revision_attempts", 2)), revision)
    return {
        "revision_count": revision,
        "max_revision_attempts": maximum,
        "revision_feedback": feedback,
        "implementation": state.get("implementation"),
        "acceptance": None,
        "review": None,
        "risk": None,
        "supervision": None,
        "blocking_reason": None,
        "pending_action": None,
        "current_stage": Stage.IMPLEMENTATION.value,
        "status": RunStatus.RUNNING.value,
        "timeline": event(
            Stage.IMPLEMENTATION,
            f"Acceptance revision {revision} started",
            "system",
            feedback[:500],
        ),
    }


def route_acceptance_recovery(state: CodingState) -> str:
    if state.get("decision") == "retry":
        return "acceptance"
    if state.get("decision") == "revise":
        return "revision"
    return "reject"
