from langgraph.graph import END, START, StateGraph

from taskhub_v2.domain.models import RunStatus, Stage
from taskhub_v2.workflows.state import StepState, event


async def normalize_requirement(state: StepState) -> dict:
    requirement = " ".join(state["requirement"].strip().split())
    return {
        "requirement": requirement,
        "current_stage": Stage.INTAKE.value,
        "status": RunStatus.RUNNING.value,
        "model_runs": [],
        "timeline": event(Stage.INTAKE, "Requirement accepted", "system", requirement[:160]),
    }


def build_intake_graph():
    builder = StateGraph(StepState)
    builder.add_node("normalize_requirement", normalize_requirement)
    builder.add_edge(START, "normalize_requirement")
    builder.add_edge("normalize_requirement", END)
    return builder.compile()
