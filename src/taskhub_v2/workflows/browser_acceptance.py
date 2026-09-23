from pathlib import Path

from taskhub_v2.domain.models import RunStatus, Stage
from taskhub_v2.workflows.state import CodingState, event


async def request_browser_acceptance(state: CodingState) -> dict:
    evidence = (state.get("acceptance") or {}).get("evidence", [])
    has_browser_evidence = any(item.get("kind") == "browser" for item in evidence)
    if has_browser_evidence:
        supervision = state.get("supervision") or {}
        findings = [supervision.get("summary", "")]
        findings.extend(supervision.get("reasons", []))
        findings = [item.strip() for item in findings if item and item.strip()]
        detail = "Windows 浏览器证据仍未获监督认可。"
        if findings:
            detail += "退回实施时必须逐项补齐以下验收要求：\n- " + "\n- ".join(findings)
        else:
            detail += "请检查证据覆盖范围后重试。"
    else:
        implementation = state.get("implementation") or {}
        workspace = implementation.get("workspace") or {}
        contract = Path(workspace.get("path", "")) / ".taskhub/acceptance.yaml"
        if contract.is_file():
            return {"status": RunStatus.RUNNING.value}
        detail = "候选版本缺少浏览器验收契约"
        if not state.get("acceptance_contract_bootstrap_attempted"):
            return {
                "status": RunStatus.RUNNING.value,
                "current_stage": Stage.ACCEPTANCE_BLOCKED.value,
                "acceptance_contract_bootstrap_attempted": True,
                "blocking_reason": {
                    "code": "acceptance_contract_missing",
                    "detail": detail,
                    "responsible_node": "implementation",
                    "model": "none",
                    "recommended_action": "系统自动返回实施环节补齐验收契约",
                },
                "timeline": event(
                    Stage.ACCEPTANCE_BLOCKED,
                    "Browser acceptance contract bootstrap requested",
                    "system",
                    detail,
                ),
            }
        detail = "自动补齐后仍缺少 .taskhub/acceptance.yaml，请检查实施结果"
    return {
        "status": RunStatus.BLOCKED.value,
        "current_stage": Stage.ACCEPTANCE_BLOCKED.value,
        "blocking_reason": {
            "code": (
                "browser_evidence_missing"
                if has_browser_evidence
                else "acceptance_contract_missing"
            ),
            "detail": detail,
            "responsible_node": (
                "windows-gui-34"
                if has_browser_evidence
                else "implementation"
            ),
            "model": "none",
            "recommended_action": (
                "按监督明细退回实施补齐专项浏览器测试，或在修复后重新执行自动验收"
                if has_browser_evidence
                else "退回实施检查验收契约"
            ),
        },
        "pending_action": {
            "type": "acceptance_recovery",
            "choices": (
                ["retry", "cancel"]
                if has_browser_evidence
                else ["revise", "cancel"]
            ),
        },
    }


def route_browser_acceptance(state: CodingState) -> str:
    if state.get("status") == RunStatus.BLOCKED.value:
        return "recovery"
    reason = state.get("blocking_reason") or {}
    return "revision" if reason.get("code") == "acceptance_contract_missing" else "execute"
