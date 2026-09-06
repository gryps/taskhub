from pathlib import Path

from taskhub_v2.domain.models import RunStatus, Stage
from taskhub_v2.workflows.state import CodingState


async def request_browser_acceptance(state: CodingState) -> dict:
    evidence = (state.get("acceptance") or {}).get("evidence", [])
    if any(item.get("kind") == "browser" for item in evidence):
        detail = "Windows 浏览器证据仍未获监督认可，请检查证据后重试"
    else:
        detail = "Windows 浏览器证据缺失，请在 .34 执行浏览器验收"
        implementation = state.get("implementation") or {}
        workspace = implementation.get("workspace") or {}
        contract = Path(workspace.get("path", "")) / ".taskhub/acceptance.yaml"
        if contract.is_file():
            return {"status": RunStatus.RUNNING.value}
    return {
        "status": RunStatus.BLOCKED.value,
        "current_stage": Stage.ACCEPTANCE_BLOCKED.value,
        "blocking_reason": {"code": "browser_evidence_missing", "detail": detail},
        "pending_action": {
            "type": "acceptance_recovery",
            "choices": ["retry", "cancel"],
        },
    }


def route_browser_acceptance(state: CodingState) -> str:
    return "recovery" if state.get("status") == RunStatus.BLOCKED.value else "execute"
