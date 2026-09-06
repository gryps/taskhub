from langgraph.graph import END, START, StateGraph

from taskhub_v2.domain.models import RunStatus, Stage
from taskhub_v2.providers.base import ModelProvider
from taskhub_v2.workflows.state import StepState, event, model_run


def build_planning_graph(provider: ModelProvider):
    async def create_plan(state: StepState) -> dict:
        result = await provider.create_plan(state["requirement"])
        plan = result.content
        return {
            "plan": plan.model_dump(),
            "current_stage": Stage.PLAN_APPROVAL.value,
            "status": RunStatus.WAITING.value,
            "model_runs": model_run("planner", result),
            "pending_action": {
                "type": "plan_approval",
                "title": "Review the implementation plan",
                "choices": ["approve", "reject"],
                "next_on_approve": "implementation",
            },
            "timeline": event(
                Stage.PLANNING,
                "Plan created",
                "planner",
                f"{len(plan.steps)} implementation steps",
            ),
        }

    builder = StateGraph(StepState)
    builder.add_node("create_plan", create_plan)
    builder.add_edge(START, "create_plan")
    builder.add_edge("create_plan", END)
    return builder.compile()
