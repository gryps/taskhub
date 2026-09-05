from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph


ACTIVE_WORKFLOW_STATES = {
    "planning",
    "awaiting_plan_approval",
    "implementation",
    "review",
    "risk",
    "awaiting_supervision",
    "blocked",
}
TERMINAL_WORKFLOW_STATES = {"completed", "canceled"}
WORKFLOW_STATES = ACTIVE_WORKFLOW_STATES | TERMINAL_WORKFLOW_STATES | {"paused"}

ROLE_FOR_STATE = {
    "planning": "planner",
    "awaiting_plan_approval": "supervisor",
    "implementation": "coder",
    "review": "reviewer",
    "risk": "risk",
    "awaiting_supervision": "supervisor",
}

ACTION_TARGETS = {
    "plan_ready": {"planning": "awaiting_plan_approval"},
    "approve_plan": {"awaiting_plan_approval": "implementation"},
    "implementation_done": {"implementation": "review"},
    "review_pass": {"review": "risk"},
    "review_reject": {"review": "implementation"},
    "risk_clear": {"risk": "awaiting_supervision"},
    "risk_block": {"risk": "blocked"},
    "approve_release": {"awaiting_supervision": "completed"},
    "request_rework": {"awaiting_supervision": "implementation"},
    "backfill_implementation": {"blocked": "implementation"},
    "block": {
        "planning": "blocked",
        "awaiting_plan_approval": "blocked",
        "implementation": "blocked",
        "review": "blocked",
        "risk": "blocked",
        "awaiting_supervision": "blocked",
    },
    "cancel": {state: "canceled" for state in ACTIVE_WORKFLOW_STATES | {"paused"}},
}


class WorkflowTransitionState(TypedDict, total=False):
    current_state: str
    action: str
    resume_state: str | None
    next_state: str
    next_resume_state: str | None
    current_role: str | None
    increments_iteration: bool


def transition_node(state: WorkflowTransitionState) -> WorkflowTransitionState:
    current = state["current_state"]
    action = state["action"]
    resume_state = state.get("resume_state")
    if current not in WORKFLOW_STATES:
        raise ValueError(f"unknown workflow state: {current}")

    if action == "pause":
        if current not in ACTIVE_WORKFLOW_STATES:
            raise ValueError(f"cannot pause workflow in state: {current}")
        target = "paused"
        next_resume = current
    elif action == "resume":
        if current != "paused" or resume_state not in ACTIVE_WORKFLOW_STATES:
            raise ValueError("paused workflow does not have a valid resume state")
        target = resume_state
        next_resume = None
    elif action == "retry":
        if current != "blocked" or resume_state not in ACTIVE_WORKFLOW_STATES - {"blocked"}:
            raise ValueError("blocked workflow does not have a valid retry state")
        target = resume_state
        next_resume = None
    else:
        target = ACTION_TARGETS.get(action, {}).get(current)
        if not target:
            raise ValueError(f"invalid workflow transition: {current} + {action}")
        next_resume = current if target == "blocked" else None

    return {
        **state,
        "next_state": target,
        "next_resume_state": next_resume,
        "current_role": ROLE_FOR_STATE.get(target),
        "increments_iteration": action in {"review_reject", "request_rework"},
    }


def build_workflow_graph():
    graph = StateGraph(WorkflowTransitionState)
    graph.add_node("transition", transition_node)
    graph.add_edge(START, "transition")
    graph.add_edge("transition", END)
    return graph.compile()


compiled_workflow_graph = build_workflow_graph()


def resolve_workflow_transition(current_state: str, action: str, resume_state: str | None = None) -> dict[str, Any]:
    return compiled_workflow_graph.invoke(
        {"current_state": current_state, "action": action, "resume_state": resume_state}
    )
