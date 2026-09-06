import json

from langgraph.graph import END, START, StateGraph

from taskhub_v2.domain.models import Stage
from taskhub_v2.providers.base import ModelProvider
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
        result = await provider.assess_risk(state["requirement"], evidence)
        return {
            "risk": result.content,
            "current_stage": Stage.RISK.value,
            "model_runs": model_run("risk", result),
            "timeline": event(Stage.RISK, "Risk assessed", "risk", result.content),
        }

    builder = StateGraph(StepState)
    builder.add_node("assess", assess)
    builder.add_edge(START, "assess")
    builder.add_edge("assess", END)
    return builder.compile()
