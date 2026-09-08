import json

from langgraph.graph import END, START, StateGraph

from taskhub_v2.domain.models import RunStatus, Stage
from taskhub_v2.providers.base import ModelProvider
from taskhub_v2.providers.fallback import ProvidersExhaustedError
from taskhub_v2.workflows.state import StepState, event, model_run


def build_risk_graph(provider: ModelProvider):
    async def assess(state: StepState) -> dict:
        evidence = json.dumps(
            {
                "implementation": state["implementation"] or {},
                "acceptance": state.get("acceptance") or {},
            },
            ensure_ascii=False,
        )
        try:
            result = await provider.assess_risk(state["requirement"], evidence)
        except ProvidersExhaustedError as exc:
            detail = str(exc)[:1200]
            return {
                "current_stage": Stage.RISK.value,
                "status": RunStatus.BLOCKED.value,
                "blocking_reason": {
                    "code": "model_resources_unavailable",
                    "detail": detail,
                    "role": exc.role,
                    "failures": exc.failures,
                },
                "pending_action": {
                    "type": "risk_recovery",
                    "title": "Risk model resources are unavailable",
                    "choices": ["retry", "cancel"],
                },
                "model_runs": [],
                "timeline": event(Stage.RISK, "Risk model resources blocked", "risk", detail),
            }
        return {
            "risk": result.content,
            "current_stage": Stage.RISK.value,
            "status": RunStatus.RUNNING.value,
            "blocking_reason": None,
            "pending_action": None,
            "model_runs": model_run("risk", result),
            "timeline": event(Stage.RISK, "Risk assessed", "risk", result.content),
        }

    builder = StateGraph(StepState)
    builder.add_node("assess", assess)
    builder.add_edge(START, "assess")
    builder.add_edge("assess", END)
    return builder.compile()
