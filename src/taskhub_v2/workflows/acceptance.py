from langgraph.graph import END, START, StateGraph

from taskhub_v2.domain.models import ExecutionResult, RunStatus, Stage
from taskhub_v2.workers.base import AcceptanceGateway
from taskhub_v2.workflows.state import StepState, event


def build_acceptance_graph(gateway: AcceptanceGateway):
    async def verify(state: StepState) -> dict:
        implementation = ExecutionResult.model_validate(state["implementation"])
        try:
            result = await gateway.verify(
                state["run_id"], state["project_id"], implementation
            )
        except Exception as exc:
            reason = getattr(exc, "reason", exc.__class__.__name__)
            detail = getattr(exc, "detail", str(exc))[:4000]
            implementation_fix = reason in {
                "acceptance_contract_invalid",
                "acceptance_contract_missing",
                "acceptance_suite_invalid",
            }
            return {
                "current_stage": Stage.ACCEPTANCE_BLOCKED.value,
                "status": RunStatus.BLOCKED.value,
                "blocking_reason": {
                    "code": reason,
                    "detail": detail,
                    "responsible_node": "implementation" if implementation_fix else "acceptance",
                    "model": "none",
                    "recommended_action": (
                        "系统退回实施环节，按平台契约模板修正后重新验收"
                        if implementation_fix
                        else "检查验收执行日志后重试或退回实施"
                    ),
                },
                "pending_action": {
                    "type": "acceptance_recovery",
                    "title": "Acceptance needs attention",
                    "choices": (
                        ["revise", "cancel"]
                        if implementation_fix
                        else ["retry", "revise", "cancel"]
                    ),
                },
                "timeline": event(
                    Stage.ACCEPTANCE_BLOCKED, "Acceptance blocked", "acceptance", detail
                ),
                "model_runs": [],
            }
        return {
            "acceptance": result.model_dump(mode="json"),
            "current_stage": Stage.ACCEPTANCE.value,
            "status": RunStatus.RUNNING.value,
            "blocking_reason": None,
            "pending_action": None,
            "timeline": event(
                Stage.ACCEPTANCE,
                "Acceptance evidence collected",
                "acceptance",
                f"{len(result.evidence)} evidence records passed",
            ),
            "model_runs": [],
        }

    builder = StateGraph(StepState)
    builder.add_node("verify", verify)
    builder.add_edge(START, "verify")
    builder.add_edge("verify", END)
    return builder.compile()
