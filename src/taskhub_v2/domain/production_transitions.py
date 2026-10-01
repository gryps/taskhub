STATE_TRANSITIONS: dict[str, dict[str, set[str]]] = {
    "requirement": {
        "submitted": {"clarification_required", "productized", "superseded"},
        "clarification_required": {"productized", "superseded"},
        "productized": {"superseded"},
    },
    "product_decision": {"pending": {"resolved", "cancelled"}},
    "project_contract": {
        "draft": {"in_review", "rejected"},
        "in_review": {"active", "draft", "rejected"},
        "active": {"superseded"},
    },
    "engineering_policy": {
        "draft": {"active", "rejected"},
        "active": {"superseded"},
    },
    "policy_exception": {
        "proposed": {"approved", "rejected"},
        "approved": {"revoked"},
    },
    "execution_batch": {
        "planned": {"running", "failed"},
        "running": {"completed", "failed"},
    },
    "dag_snapshot": {
        "planning": {"running", "waiting", "blocked"},
        "running": {"running", "waiting", "completed", "blocked"},
        "waiting": {"running", "waiting", "blocked"},
    },
    "product_spec": {
        "draft": {"in_review", "rejected"},
        "in_review": {"approved", "rejected", "draft"},
        "approved": {"superseded"},
    },
    "execution_plan": {
        "draft": {"validating"},
        "validating": {"active", "invalid", "draft"},
        "active": {"completed", "superseded"},
    },
    "task": {
        "pending": {"ready", "blocked", "cancelled", "superseded"},
        "ready": {"assigned", "blocked", "cancelled", "superseded"},
        "assigned": {"running", "ready", "blocked", "cancelled"},
        "running": {"verifying", "blocked", "revision_ready", "cancelled"},
        "verifying": {"completed", "blocked", "revision_ready"},
        "blocked": {"ready", "revision_ready", "cancelled", "superseded"},
        "revision_ready": {"ready", "cancelled", "superseded"},
    },
    "task_attempt": {
        "created": {"dispatched", "abandoned"},
        "dispatched": {"running", "failed", "timed_out", "abandoned"},
        "running": {"result_received", "failed", "timed_out", "abandoned"},
        "result_received": {"validated", "failed"},
    },
    "change_request": {
        "proposed": {"approved", "rejected"},
        "approved": {"applied", "rejected"},
    },
    "capability_pack": {
        "draft": {"trusted", "rejected"},
        "trusted": {"disabled"},
        "disabled": {"trusted"},
    },
    "capability_pack_lock": {
        "draft": {"active", "rejected"},
        "active": {"superseded"},
    },
    "project_design_contract": {"active": {"superseded"}},
}


def validate_transition(object_type: str, previous: str, target: str) -> None:
    if previous == target:
        return
    if target not in STATE_TRANSITIONS.get(object_type, {}).get(previous, set()):
        raise ValueError(f"illegal {object_type} state transition: {previous} -> {target}")
