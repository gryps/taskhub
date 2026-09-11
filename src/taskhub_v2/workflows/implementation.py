from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from taskhub_v2.domain.models import Plan, RunStatus, Stage
from taskhub_v2.workers.base import WorkerGateway
from taskhub_v2.workflows.state import CodingState, StepState, event, execution_requirement


def implementation_recovery_feedback(reason: dict) -> str:
    code = reason.get("code", "implementation_failed")
    detail = reason.get("detail", "")
    return (
        f"The previous implementation attempt was blocked with {code}. "
        "Fix the concrete failure below, preserve the existing implementation, and rerun "
        f"the failing command before returning.\n\n{detail}"
    )[-16_000:]


async def prepare_test_failure_revision(state: CodingState) -> dict:
    reason = state.get("blocking_reason") or {}
    revision = int(state.get("revision_count", 0)) + 1
    feedback = implementation_recovery_feedback(reason)
    return {
        "revision_count": revision,
        "revision_feedback": feedback,
        "current_stage": Stage.IMPLEMENTATION.value,
        "status": RunStatus.RUNNING.value,
        "pending_action": None,
        "blocking_reason": None,
        "timeline": event(
            Stage.IMPLEMENTATION,
            f"Automatic test-failure revision {revision} started",
            "system",
            feedback[:500],
        ),
    }


async def recover_implementation(state: CodingState) -> dict:
    reason = state.get("blocking_reason") or {}
    response = interrupt(
        {
            "type": "implementation_recovery",
            "run_id": state["run_id"],
            "reason": reason,
            "choices": ["retry", "cancel"],
        }
    )
    decision = response.get("decision") if isinstance(response, dict) else response
    retry = decision == "retry"
    return {
        "decision": decision,
        "attempt": int(state.get("attempt", 0)) + (1 if retry else 0),
        "pending_action": None,
        "blocking_reason": None if retry else reason,
        "revision_feedback": (
            implementation_recovery_feedback(reason)
            if retry
            else state.get("revision_feedback", "")
        ),
        "current_stage": Stage.IMPLEMENTATION.value if retry else Stage.REJECTED.value,
        "status": RunStatus.RUNNING.value if retry else RunStatus.REJECTED.value,
        "timeline": event(
            Stage.IMPLEMENTATION_BLOCKED,
            "Implementation retry requested" if retry else "Run cancelled",
            "owner",
        ),
    }


def build_implementation_graph(worker: WorkerGateway):
    async def implement(state: StepState) -> dict:
        plan = Plan.model_validate(state["plan"])
        try:
            result = await worker.execute(
                state["run_id"],
                state["project_id"],
                execution_requirement(state),
                plan,
                revision=int(state.get("revision_count", 0)),
                feedback=state.get("revision_feedback", ""),
            )
        except Exception as exc:
            reason = getattr(exc, "reason", exc.__class__.__name__)
            detail = getattr(exc, "detail", str(exc))[:16_000]
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
            if getattr(exc, "diagnostics", []):
                blocking_reason["diagnostics"] = exc.diagnostics
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
