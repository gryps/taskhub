from langgraph.types import interrupt

from taskhub_v2.domain.models import RunStatus, Stage
from taskhub_v2.workflows.state import CodingState, event


async def handle_revision_limit(state: CodingState) -> dict:
    manual = (state.get("pending_action") or {}).get("type") == "manual_intervention"
    evidence_only = bool((state.get("supervision") or {}).get("missing_evidence"))
    response = interrupt(
        {
            "type": "manual_intervention" if manual else "revision_limit",
            "run_id": state["run_id"],
            "revision_count": state.get("revision_count", 0),
            "supervision": state.get("supervision"),
            "choices": (
                ["reassess", "retry", "cancel"]
                if manual
                else (
                    ["recheck", "reassess", "manual", "cancel"]
                    if evidence_only
                    else ["reassess", "retry", "manual", "cancel"]
                )
            ),
        }
    )
    decision = response.get("decision") if isinstance(response, dict) else response
    if decision == "manual":
        return {
            "decision": decision,
            "pending_action": {
                "type": "manual_intervention",
                "title": "Platform or environment remediation required",
                "description": (
                    "Repair TaskHub, node environment, or configuration, then retry. "
                    "Do not modify the managed-project implementation manually."
                ),
                "choices": ["reassess", "retry", "cancel"],
            },
            "current_stage": Stage.SUPERVISION.value,
            "status": RunStatus.WAITING.value,
            "timeline": event(
                Stage.SUPERVISION,
                "Transferred for platform or environment remediation",
                "owner",
                response.get("comment", "") if isinstance(response, dict) else "",
            ),
        }

    retry = decision == "retry"
    recheck = decision == "recheck"
    reassess = decision == "reassess"
    submitted = response.get("evidence", []) if isinstance(response, dict) else []
    existing = (state.get("acceptance") or {}).get("evidence", [])
    combined = [*existing, *submitted]
    return {
        "decision": decision,
        "max_revision_attempts": (
            int(state.get("max_revision_attempts", 2)) + 1
            if retry
            else int(state.get("max_revision_attempts", 2))
        ),
        "acceptance": (
            {
                "status": (
                    "passed"
                    if combined and all(item.get("status") == "passed" for item in combined)
                    else "failed"
                ),
                "evidence": combined,
            }
            if reassess
            else state.get("acceptance")
        ),
        "pending_action": None,
        "current_stage": (
            Stage.ACCEPTANCE.value
            if recheck
            else Stage.REVIEW.value
            if reassess
            else Stage.IMPLEMENTATION.value
            if retry
            else Stage.REJECTED.value
        ),
        "status": (
            RunStatus.RUNNING.value
            if retry or recheck or reassess else RunStatus.REJECTED.value
        ),
        "timeline": event(
            Stage.SUPERVISION,
            (
                "Acceptance evidence recollection requested"
                if recheck
                else "Acceptance evidence submitted"
                if reassess
                else "Extra revision approved"
                if retry
                else "Run cancelled"
            ),
            "owner",
            response.get("comment", "") if isinstance(response, dict) else "",
        ),
    }


def route_revision_limit(state: CodingState) -> str:
    decision = state.get("decision")
    if decision == "recheck":
        return "acceptance"
    if decision == "manual":
        return "manual"
    if decision == "reassess":
        return "review"
    return "revision" if decision == "retry" else "reject"
