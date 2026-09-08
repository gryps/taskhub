from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from taskhub_v2.domain.models import ExecutionResult, RunStatus, Stage
from taskhub_v2.providers.base import ModelProvider
from taskhub_v2.workers.acceptance import LocalAcceptanceGateway
from taskhub_v2.workers.base import AcceptanceGateway, PublisherGateway, WorkerGateway
from taskhub_v2.workers.publisher import LocalPublisher
from taskhub_v2.workflows.acceptance import build_acceptance_graph
from taskhub_v2.workflows.acceptance_recovery import (
    prepare_acceptance_revision,
    recover_acceptance,
    route_acceptance_recovery,
)
from taskhub_v2.workflows.browser_acceptance import (
    request_browser_acceptance,
    route_browser_acceptance,
)
from taskhub_v2.workflows.implementation import build_implementation_graph
from taskhub_v2.workflows.intake import build_intake_graph
from taskhub_v2.workflows.manual_handoff import handle_revision_limit, route_revision_limit
from taskhub_v2.workflows.planning import build_planning_graph
from taskhub_v2.workflows.review import build_review_graph
from taskhub_v2.workflows.risk import build_risk_graph
from taskhub_v2.workflows.state import CodingState, event
from taskhub_v2.workflows.supervisor import build_supervisor_graph


def _implementation_recovery_feedback(reason: dict) -> str:
    code = reason.get("code", "implementation_failed")
    detail = reason.get("detail", "")
    return (
        f"The previous implementation attempt was blocked with {code}. "
        "Fix the concrete failure below, preserve the existing implementation, and rerun "
        f"the failing command before returning.\n\n{detail}"
    )[-16_000:]


def build_main_graph(
    provider: ModelProvider,
    worker: WorkerGateway,
    checkpointer,
    publisher: PublisherGateway | None = None,
    acceptance: AcceptanceGateway | None = None,
):
    publisher = publisher or LocalPublisher()
    acceptance = acceptance or LocalAcceptanceGateway()

    async def request_plan_approval(state: CodingState) -> dict:
        response = interrupt(
            {
                "type": "plan_approval",
                "run_id": state["run_id"],
                "plan": state["plan"],
                "prompt": "Approve this implementation plan?",
            }
        )
        decision = response.get("decision") if isinstance(response, dict) else response
        approved = decision == "approve"
        return {
            "decision": decision,
            "pending_action": None,
            "current_stage": (
                Stage.IMPLEMENTATION.value if approved else Stage.REJECTED.value
            ),
            "status": RunStatus.RUNNING.value if approved else RunStatus.REJECTED.value,
            "timeline": event(
                Stage.PLAN_APPROVAL,
                "Plan approved" if approved else "Plan rejected",
                "owner",
                response.get("comment", "") if isinstance(response, dict) else "",
            ),
        }

    async def reject(state: CodingState) -> dict:
        return {
            "current_stage": Stage.REJECTED.value,
            "status": RunStatus.REJECTED.value,
            "timeline": event(Stage.REJECTED, "Run stopped", "system"),
        }

    async def recover_implementation(state: CodingState) -> dict:
        reason = state.get("blocking_reason") or {}
        response = interrupt(
            {
                "type": "implementation_recovery",
                "run_id": state["run_id"],
                "reason": state.get("blocking_reason"),
                "choices": ["retry", "cancel"],
            }
        )
        decision = response.get("decision") if isinstance(response, dict) else response
        retry = decision == "retry"
        return {
            "decision": decision,
            "attempt": int(state.get("attempt", 0)) + (1 if retry else 0),
            "pending_action": None,
            "blocking_reason": None if retry else state.get("blocking_reason"),
            "revision_feedback": (
                _implementation_recovery_feedback(reason) if retry
                else state.get("revision_feedback", "")
            ),
            "current_stage": (
                Stage.IMPLEMENTATION.value if retry else Stage.REJECTED.value
            ),
            "status": RunStatus.RUNNING.value if retry else RunStatus.REJECTED.value,
            "timeline": event(
                Stage.IMPLEMENTATION_BLOCKED,
                "Implementation retry requested" if retry else "Run cancelled",
                "owner",
            ),
        }

    async def recover_supervision(state: CodingState) -> dict:
        response = interrupt(
            {
                "type": "supervision_recovery",
                "run_id": state["run_id"],
                "reason": state.get("blocking_reason"),
                "choices": ["retry", "cancel"],
            }
        )
        decision = response.get("decision") if isinstance(response, dict) else response
        retry = decision == "retry"
        return {
            "decision": decision,
            "pending_action": None,
            "blocking_reason": None if retry else state.get("blocking_reason"),
            "current_stage": (
                Stage.SUPERVISION.value if retry else Stage.REJECTED.value
            ),
            "status": RunStatus.RUNNING.value if retry else RunStatus.REJECTED.value,
            "timeline": event(
                Stage.SUPERVISION,
                "Supervisor retry requested" if retry else "Run cancelled",
                "owner",
                response.get("comment", "") if isinstance(response, dict) else "",
            ),
        }

    async def request_merge_approval(state: CodingState) -> dict:
        response = interrupt(
            {
                "type": "merge_approval",
                "run_id": state["run_id"],
                "implementation": state.get("implementation"),
                "supervision": state.get("supervision"),
                "choices": ["approve", "reject"],
            }
        )
        decision = response.get("decision") if isinstance(response, dict) else response
        approved = decision == "approve"
        return {
            "decision": decision,
            "pending_action": None,
            "current_stage": Stage.MERGING.value if approved else Stage.REJECTED.value,
            "status": RunStatus.RUNNING.value if approved else RunStatus.REJECTED.value,
            "timeline": event(
                Stage.MERGE_APPROVAL,
                "Publication approved" if approved else "Publication rejected",
                "owner",
                response.get("comment", "") if isinstance(response, dict) else "",
            ),
        }

    async def prepare_revision(state: CodingState) -> dict:
        supervision = state.get("supervision") or {}
        reasons = supervision.get("reasons") or []
        feedback = "\n".join(
            [supervision.get("summary", "Supervisor requested changes"), *reasons]
        )
        revision = int(state.get("revision_count", 0)) + 1
        return {
            "revision_count": revision,
            "revision_feedback": feedback,
            "implementation": state.get("implementation"),
            "acceptance": None,
            "review": None,
            "risk": None,
            "supervision": None,
            "current_stage": Stage.IMPLEMENTATION.value,
            "status": RunStatus.RUNNING.value,
            "pending_action": None,
            "timeline": event(
                Stage.IMPLEMENTATION,
                f"Revision {revision} started",
                "system",
                feedback[:500],
            ),
        }

    async def publish(state: CodingState) -> dict:
        implementation = ExecutionResult.model_validate(state["implementation"])
        try:
            result = await publisher.publish(
                state["run_id"], state["project_id"], implementation
            )
        except Exception as exc:
            reason = getattr(exc, "reason", exc.__class__.__name__)
            detail = getattr(exc, "detail", str(exc))[:500]
            return {
                "current_stage": Stage.MERGE_BLOCKED.value,
                "status": RunStatus.BLOCKED.value,
                "blocking_reason": dict(
                    code=reason, detail=detail, responsible_node=implementation.execution_node or "publisher",
                    model=implementation.model_run.model if implementation.model_run else "none", recommended_action="retry publication or cancel the task", retry_after_seconds=max(0, int(getattr(exc, "retry_after_seconds", 0)))),
                "pending_action": {
                    "type": "publication_recovery",
                    "title": "Publication needs attention",
                    "choices": ["retry", "cancel"],
                },
                "timeline": event(
                    Stage.MERGE_BLOCKED, "Publication blocked", "publisher", detail
                ),
            }
        return {
            "publication": result.model_dump(mode="json"),
            "current_stage": Stage.COMPLETED.value,
            "status": RunStatus.COMPLETED.value,
            "blocking_reason": None,
            "pending_action": None,
            "timeline": event(
                Stage.COMPLETED,
                "Published to authority",
                "publisher",
                f"{result.authority_ref} -> {result.published_commit[:12]}",
            ),
        }

    async def recover_publication(state: CodingState) -> dict:
        response = interrupt(
            {
                "type": "publication_recovery",
                "run_id": state["run_id"],
                "reason": state.get("blocking_reason"),
                "choices": ["retry", "cancel"],
            }
        )
        decision = response.get("decision") if isinstance(response, dict) else response
        retry = decision == "retry"
        return {
            "decision": decision,
            "pending_action": None,
            "blocking_reason": None if retry else state.get("blocking_reason"),
            "current_stage": Stage.MERGING.value if retry else Stage.REJECTED.value,
            "status": RunStatus.RUNNING.value if retry else RunStatus.REJECTED.value,
            "timeline": event(
                Stage.MERGE_BLOCKED,
                "Publication retry requested" if retry else "Run cancelled",
                "owner",
            ),
        }

    def route_approval(state: CodingState) -> str:
        return "implementation" if state.get("decision") == "approve" else "reject"

    def route_implementation(state: CodingState) -> str:
        return "recovery" if state.get("status") == RunStatus.BLOCKED else "acceptance"

    def route_acceptance(state: CodingState) -> str:
        return "recovery" if state.get("status") == RunStatus.BLOCKED else "review"

    def route_recovery(state: CodingState) -> str:
        return "implementation" if state.get("decision") == "retry" else "reject"

    def route_supervisor(state: CodingState) -> str:
        if state.get("status") == RunStatus.BLOCKED:
            return "recovery"
        supervision = state.get("supervision") or {}
        if supervision.get("decision") == "approve":
            return "merge_approval"
        missing_evidence = supervision.get("missing_evidence", [])
        if missing_evidence == ["browser"]:
            return "browser_acceptance"
        if missing_evidence:
            return "revision_limit"
        if int(state.get("revision_count", 0)) < int(
            state.get("max_revision_attempts", 2)
        ):
            return "revision"
        return "revision_limit"
    def route_supervision_recovery(state: CodingState) -> str:
        return "supervisor" if state.get("decision") == "retry" else "reject"

    def route_merge_approval(state: CodingState) -> str:
        return "publication" if state.get("decision") == "approve" else "reject"

    def route_publication(state: CodingState) -> str:
        return "recovery" if state.get("status") == RunStatus.BLOCKED else "done"

    def route_publication_recovery(state: CodingState) -> str:
        return "publication" if state.get("decision") == "retry" else "reject"

    builder = StateGraph(CodingState)
    builder.add_node("intake", build_intake_graph())
    builder.add_node("planning", build_planning_graph(provider))
    builder.add_node("plan_approval", request_plan_approval)
    builder.add_node("implementation", build_implementation_graph(worker))
    builder.add_node("acceptance", build_acceptance_graph(acceptance))
    builder.add_node("review", build_review_graph(provider))
    builder.add_node("risk", build_risk_graph(provider))
    builder.add_node("browser_acceptance", request_browser_acceptance)
    builder.add_conditional_edges(
        "browser_acceptance",
        route_browser_acceptance,
        {
            "recovery": "acceptance_recovery",
            "revision": "acceptance_revision",
            "execute": "acceptance",
        },
    )
    builder.add_node("supervisor", build_supervisor_graph(provider))
    builder.add_node("supervision_recovery", recover_supervision)
    builder.add_node("revision", prepare_revision)
    builder.add_node("revision_limit", handle_revision_limit)
    builder.add_node("merge_approval", request_merge_approval)
    builder.add_node("publication", publish)
    builder.add_node("publication_recovery", recover_publication)
    builder.add_node("implementation_recovery", recover_implementation)
    builder.add_node("acceptance_recovery", recover_acceptance)
    builder.add_node("acceptance_revision", prepare_acceptance_revision)
    builder.add_node("reject", reject)

    builder.add_edge(START, "intake")
    builder.add_edge("intake", "planning")
    builder.add_edge("planning", "plan_approval")
    builder.add_conditional_edges(
        "plan_approval", route_approval, {"implementation": "implementation", "reject": "reject"}
    )
    builder.add_conditional_edges(
        "implementation",
        route_implementation,
        {"recovery": "implementation_recovery", "acceptance": "acceptance"},
    )
    builder.add_conditional_edges(
        "implementation_recovery",
        route_recovery,
        {"implementation": "implementation", "reject": "reject"},
    )
    builder.add_conditional_edges(
        "acceptance",
        route_acceptance,
        {"recovery": "acceptance_recovery", "review": "review"},
    )
    builder.add_conditional_edges(
        "acceptance_recovery",
        route_acceptance_recovery,
        {
            "acceptance": "acceptance",
            "revision": "acceptance_revision",
            "reject": "reject",
        },
    )
    builder.add_edge("acceptance_revision", "implementation")
    builder.add_edge("review", "risk")
    builder.add_edge("risk", "supervisor")
    builder.add_conditional_edges(
        "supervisor",
        route_supervisor,
        {
            "recovery": "supervision_recovery",
            "merge_approval": "merge_approval",
            "browser_acceptance": "browser_acceptance",
            "revision": "revision",
            "revision_limit": "revision_limit",
        },
    )
    builder.add_conditional_edges(
        "supervision_recovery",
        route_supervision_recovery,
        {"supervisor": "supervisor", "reject": "reject"},
    )
    builder.add_edge("revision", "implementation")
    builder.add_conditional_edges(
        "revision_limit",
        route_revision_limit,
        {
            "manual": "revision_limit",
            "review": "review",
            "revision": "revision",
            "reject": "reject",
        },
    )
    builder.add_conditional_edges(
        "merge_approval",
        route_merge_approval,
        {"publication": "publication", "reject": "reject"},
    )
    builder.add_conditional_edges(
        "publication",
        route_publication,
        {"recovery": "publication_recovery", "done": END},
    )
    builder.add_conditional_edges(
        "publication_recovery",
        route_publication_recovery,
        {"publication": "publication", "reject": "reject"},
    )
    builder.add_edge("reject", END)
    return builder.compile(checkpointer=checkpointer)
