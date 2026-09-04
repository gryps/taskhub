from __future__ import annotations

from typing import Any


CONTRACT_VERSION = "taskhub.handoff/v1"
STRICT_TASK_TYPES = {"market.price.collect", "commerce.listing.draft"}

TASK_CONTRACTS: dict[str, dict[str, Any]] = {
    "market.price.collect": {
        "producer": "planner",
        "consumer": "worker-31-34-gui",
        "required_input": ["keyword", "count"],
        "required_result": ["status", "task_id", "artifacts"],
        "result_statuses": ["collected"],
    },
    "commerce.listing.draft": {
        "producer": "planner",
        "consumer": "worker-31-34-gui",
        "required_input": ["listing_task_id"],
        "required_result": ["status", "artifacts"],
        "result_statuses": ["draft_saved", "waiting_category"],
        "human_release_required": True,
    },
}


def handoff_contract(task_type: str, task_input: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    definition = TASK_CONTRACTS.get(task_type, {})
    return {
        "version": CONTRACT_VERSION,
        "task_type": task_type,
        "producer": str(metadata.get("handoff_from") or definition.get("producer") or "taskhub"),
        "consumer": str(metadata.get("handoff_to") or definition.get("consumer") or "assigned-worker"),
        "required_input": list(definition.get("required_input") or []),
        "required_result": list(definition.get("required_result") or []),
        "human_release_required": bool(definition.get("human_release_required")),
        "strict": task_type in STRICT_TASK_TYPES,
        "input_keys": sorted(task_input),
    }


def validate_task_input(task_type: str, task_input: dict[str, Any]) -> None:
    definition = TASK_CONTRACTS.get(task_type)
    if not definition:
        return
    missing = [key for key in definition["required_input"] if task_input.get(key) is None or task_input.get(key) == ""]
    if missing:
        raise ValueError(f"{task_type} input is missing required fields: {', '.join(missing)}")
    if task_type == "market.price.collect":
        count = task_input.get("count")
        if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 100:
            raise ValueError("market.price.collect count must be an integer between 1 and 100")


def validate_task_result(task_type: str, result: dict[str, Any]) -> dict[str, Any]:
    definition = TASK_CONTRACTS.get(task_type)
    if not definition:
        return {"valid": True, "strict": False, "errors": []}
    errors: list[str] = []
    for key in definition["required_result"]:
        if result.get(key) is None or result.get(key) == "":
            errors.append(f"missing result field: {key}")
    if result.get("status") not in definition["result_statuses"]:
        errors.append(f"invalid result status: {result.get('status') or 'missing'}")
    if "artifacts" in definition["required_result"] and not isinstance(result.get("artifacts"), list):
        errors.append("result artifacts must be a list")
    return {"valid": not errors, "strict": True, "errors": errors}
