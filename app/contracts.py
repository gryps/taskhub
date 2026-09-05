from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit


CONTRACT_VERSION = "taskhub.handoff/v1"
STRICT_TASK_TYPES = {"code.change", "test.run", "quality.env.check", "h5.inspect"}
TASK_CONTRACTS: dict[str, dict[str, Any]] = {
    "code.change": {
        "producer": "planner",
        "consumer": "pipeline-worker",
        "required_input": ["requirement"],
        "required_result": ["will_modify_files", "workspace_id", "baseline_commit", "diff_sha256", "touched_paths"],
        "human_release_required": True,
    },
    "test.run": {
        "producer": "pipeline-worker",
        "consumer": "pipeline-worker",
        "required_input": ["command"],
        "required_result": ["passed", "workspace_id", "workcopy_head", "workcopy_status_sha256"],
        "human_release_required": False,
    },
    "quality.env.check": {
        "producer": "planner",
        "consumer": "pipeline-worker",
        "required_input": [],
        "required_result": ["passed", "workspace_id", "workcopy_head", "workcopy_status_sha256"],
        "human_release_required": False,
    },
    "h5.inspect": {
        "producer": "planner",
        "consumer": "worker-31-34-gui",
        "required_input": ["url", "allowed_host_confirmed", "login_environment_confirmed"],
        "required_result": ["passed"],
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
    if task_type == "h5.inspect":
        parsed = urlsplit(str(task_input["url"]))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("h5.inspect url must be an absolute HTTP(S) URL")
        if task_input.get("allowed_host_confirmed") is not True:
            raise ValueError("h5.inspect allowed host must be confirmed by a human")
        if task_input.get("login_environment_confirmed") is not True:
            raise ValueError("h5.inspect login environment must be confirmed by a human")
    if task_type == "code.change" and str(task_input.get("mode") or "execute") != "execute":
        raise ValueError("code.change mode must be execute")


def validate_task_result(task_type: str, result: dict[str, Any]) -> dict[str, Any]:
    definition = TASK_CONTRACTS.get(task_type)
    if not definition:
        return {"valid": True, "strict": False, "errors": []}
    errors: list[str] = []
    for key in definition["required_result"]:
        if result.get(key) is None or result.get(key) == "":
            errors.append(f"missing result field: {key}")
    result_statuses = definition.get("result_statuses") or []
    if result_statuses and result.get("status") not in result_statuses:
        errors.append(f"invalid result status: {result.get('status') or 'missing'}")
    if "artifacts" in definition["required_result"] and not isinstance(result.get("artifacts"), list):
        errors.append("result artifacts must be a list")
    return {"valid": not errors, "strict": True, "errors": errors}
