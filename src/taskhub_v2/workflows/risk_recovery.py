from langgraph.types import interrupt

from taskhub_v2.domain.models import RunStatus, Stage
from taskhub_v2.workflows.state import CodingState, event


async def recover_risk(state: CodingState) -> dict:
    response = interrupt({
        "type": "risk_recovery",
        "run_id": state["run_id"],
        "reason": state.get("blocking_reason"),
        "choices": ["retry", "cancel"],
    })
    decision = response.get("decision") if isinstance(response, dict) else response
    retry = decision == "retry"
    return {
        "decision": decision,
        "pending_action": None,
        "blocking_reason": None if retry else state.get("blocking_reason"),
        "current_stage": Stage.RISK.value if retry else Stage.REJECTED.value,
        "status": RunStatus.RUNNING.value if retry else RunStatus.REJECTED.value,
        "timeline": event(
            Stage.RISK, "Risk assessment retry requested" if retry else "Run cancelled",
            "owner", response.get("comment", "") if isinstance(response, dict) else "",
        ),
    }


def route_risk_recovery(state: CodingState) -> str:
    return "risk" if state.get("decision") == "retry" else "reject"
