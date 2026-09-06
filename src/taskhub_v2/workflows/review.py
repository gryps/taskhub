import json

from langgraph.graph import END, START, StateGraph

from taskhub_v2.domain.models import Stage
from taskhub_v2.providers.base import ModelProvider
from taskhub_v2.workflows.state import StepState, event, model_run


def build_review_graph(provider: ModelProvider):
    async def review(state: StepState) -> dict:
        evidence = json.dumps(
            {
                "implementation": state["implementation"] or {},
                "acceptance": state.get("acceptance") or {},
            },
            ensure_ascii=False,
        )
        result = await provider.review(state["requirement"], evidence)
        return {
            "review": result.content,
            "current_stage": Stage.REVIEW.value,
            "model_runs": model_run("reviewer", result),
            "timeline": event(Stage.REVIEW, "Review completed", "reviewer", result.content),
        }

    builder = StateGraph(StepState)
    builder.add_node("review", review)
    builder.add_edge(START, "review")
    builder.add_edge("review", END)
    return builder.compile()
