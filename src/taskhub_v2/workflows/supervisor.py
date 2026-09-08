import json

from langgraph.graph import END, START, StateGraph

from taskhub_v2.domain.models import RunStatus, Stage
from taskhub_v2.providers.base import ModelProvider
from taskhub_v2.providers.fallback import ProvidersExhaustedError
from taskhub_v2.workflows.state import StepState, event, model_run


def build_supervisor_graph(provider: ModelProvider):
    async def finalize(state: StepState) -> dict:
        try:
            result = await provider.supervise(
                state["requirement"],
                json.dumps(
                    {
                        "implementation": state.get("implementation") or {},
                        "acceptance": state.get("acceptance") or {},
                    },
                    ensure_ascii=False,
                ),
                state.get("review") or "",
                state.get("risk") or "",
            )
        except ProvidersExhaustedError as exc:
            detail = str(exc)[:1200]
            return {
                "current_stage": Stage.SUPERVISION.value,
                "status": RunStatus.BLOCKED.value,
                "blocking_reason": {
                    "code": "model_resources_unavailable",
                    "detail": detail,
                    "role": exc.role,
                    "failures": exc.failures,
                },
                "pending_action": {
                    "type": "supervision_recovery",
                    "title": "Supervisor model resources are unavailable",
                    "choices": ["retry", "cancel"],
                },
                "model_runs": [],
                "timeline": event(
                    Stage.SUPERVISION,
                    "Supervisor model resources blocked",
                    "supervisor",
                    detail,
                ),
            }
        decision = result.content
        approved = decision.decision == "approve"
        evidence_only = bool(decision.missing_evidence)
        revision_available = int(state.get("revision_count", 0)) < int(
            state.get("max_revision_attempts", 2)
        )
        return {
            "supervision": decision.model_dump(),
            "blocking_reason": None,
            "current_stage": (
                Stage.MERGE_APPROVAL.value if approved else Stage.SUPERVISION.value
            ),
            "status": (
                RunStatus.WAITING.value
                if approved or evidence_only or not revision_available
                else RunStatus.RUNNING.value
            ),
            "pending_action": (
                {
                    "type": "merge_approval",
                    "title": "Publish the approved change",
                    "choices": ["approve", "reject"],
                }
                if approved
                else (
                    {
                        "type": "revision_limit",
                        "title": (
                            "Acceptance evidence required"
                            if evidence_only
                            else "Automatic revision limit reached"
                        ),
                        "choices": ["reassess", "retry", "manual", "cancel"],
                    }
                    if evidence_only or not revision_available
                    else None
                )
            ),
            "model_runs": model_run("supervisor", result),
            "timeline": event(
                Stage.SUPERVISION,
                (
                    "Change accepted"
                    if approved
                    else (
                        "Revision requested"
                        if revision_available
                        else "Revision limit reached"
                    )
                ),
                "supervisor",
                decision.summary,
            ),
        }

    builder = StateGraph(StepState)
    builder.add_node("finalize", finalize)
    builder.add_edge(START, "finalize")
    builder.add_edge("finalize", END)
    return builder.compile()
