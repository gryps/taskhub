from langgraph.graph import END, START, StateGraph

from taskhub_v2.domain.models import Plan, RunStatus, Stage
from taskhub_v2.workers.base import WorkerGateway
from taskhub_v2.workflows.state import StepState, event


def build_implementation_graph(worker: WorkerGateway):
    async def implement(state: StepState) -> dict:
        plan = Plan.model_validate(state["plan"])
        try:
            result = await worker.execute(
                state["run_id"],
                state["project_id"],
                state["requirement"],
                plan,
                revision=int(state.get("revision_count", 0)),
                feedback=state.get("revision_feedback", ""),
            )
        except Exception as exc:
            reason = getattr(exc, "reason", exc.__class__.__name__)
            detail = getattr(exc, "detail", str(exc))[:500]
            failed_model_runs = [
                {
                    "role": "coder",
                    "provider": result.provider,
                    "model": result.model,
                    "duration_ms": result.duration_ms,
                    "failed_providers": result.failed_providers,
                }
                for result in getattr(exc, "model_results", [])
            ]
            blocking_reason = {"code": reason, "detail": detail}
            if getattr(exc, "model_results", []):
                blocking_reason["attempts"] = [
                    {
                        "provider": result.provider,
                        "model": result.model,
                        "duration_ms": result.duration_ms,
                        "summary": getattr(result.content, "summary", str(result.content)),
                        "failed_providers": result.failed_providers,
                    }
                    for result in exc.model_results
                ]
            return {
                "current_stage": Stage.IMPLEMENTATION_BLOCKED.value,
                "status": RunStatus.BLOCKED.value,
                "blocking_reason": blocking_reason,
                "pending_action": {
                    "type": "implementation_recovery",
                    "title": "Implementation needs attention",
                    "choices": ["retry", "cancel"],
                },
                "model_runs": failed_model_runs,
                "timeline": event(
                    Stage.IMPLEMENTATION_BLOCKED,
                    "Implementation blocked",
                    "implementation",
                    detail,
                ),
            }
        return {
            "implementation": result.model_dump(mode="json"),
            "current_stage": Stage.IMPLEMENTATION.value,
            "status": RunStatus.RUNNING.value,
            "blocking_reason": None,
            "pending_action": None,
            "model_runs": [result.model_run.model_dump()] if result.model_run else [],
            "timeline": event(
                Stage.IMPLEMENTATION,
                "Revision completed" if state.get("revision_count", 0) else "Worker completed",
                "implementation",
                result.summary,
            ),
        }

    builder = StateGraph(StepState)
    builder.add_node("worker_execute", implement)
    builder.add_edge(START, "worker_execute")
    builder.add_edge("worker_execute", END)
    return builder.compile()
