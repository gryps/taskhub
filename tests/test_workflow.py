import asyncio

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from taskhub_v2.domain.models import (
    ApprovalRequest,
    ExecutionResult,
    ModelResult,
    PublicationResult,
    ResumeRequest,
    RunStatus,
    Stage,
    StartRunRequest,
    SupervisionDecision,
)
from taskhub_v2.services.runs import RunConflictError, RunService
from taskhub_v2.workflows import build_main_graph
from tests.fakes import RecordingProvider, RecordingWorker


class RecoveringWorker(RecordingWorker):
    async def execute(
        self, run_id, project_id, requirement, plan, revision=0, feedback=""
    ):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary worker failure")
        return ExecutionResult(summary="recovered")


class RecoveringPublisher:
    def __init__(self):
        self.calls = 0

    async def publish(self, run_id, project_id, implementation):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary publication failure")
        return PublicationResult(
            project_id=project_id,
            authority_ref="main",
            previous_commit="before",
            published_commit="after",
            branch=f"taskhub/{run_id}",
        )


class RejectOnceProvider(RecordingProvider):
    async def supervise(self, requirement, implementation, review, risk):
        self.supervisor_calls += 1
        decision = "reject" if self.supervisor_calls == 1 else "approve"
        return ModelResult(
            content=SupervisionDecision(
                decision=decision,
                summary="Missing boundary test" if decision == "reject" else "accepted",
                reasons=["Add the boundary test"] if decision == "reject" else ["tests pass"],
            ),
            provider="recording",
            model="test",
        )


class AlwaysRejectProvider(RejectOnceProvider):
    async def supervise(self, requirement, implementation, review, risk):
        self.supervisor_calls += 1
        return ModelResult(
            content=SupervisionDecision(
                decision="reject",
                summary="A material defect remains",
                reasons=["Fix the defect"],
            ),
            provider="recording",
            model="test",
        )


class RevisionRecordingWorker(RecordingWorker):
    def __init__(self):
        super().__init__()
        self.revisions = []

    async def execute(
        self, run_id, project_id, requirement, plan, revision=0, feedback=""
    ):
        self.calls += 1
        self.revisions.append((revision, feedback))
        return ExecutionResult(summary=f"revision={revision}")


def make_service(checkpointer=None):
    provider = RecordingProvider()
    worker = RecordingWorker()
    graph = build_main_graph(provider, worker, checkpointer or InMemorySaver())
    return RunService(graph), provider, worker


def test_run_pauses_for_plan_approval_then_completes():
    async def scenario():
        service, provider, worker = make_service()

        waiting = await service.start(
            StartRunRequest(project_id="shop", requirement="Add product search")
        )

        assert waiting.stage == Stage.PLAN_APPROVAL
        assert waiting.status == RunStatus.WAITING
        assert waiting.next_nodes == ["plan_approval"]
        assert waiting.pending_action["type"] == "plan_approval"
        assert provider.plan_calls == 1
        assert worker.calls == 0
        assert waiting.model_runs[0].role == "planner"
        assert waiting.model_runs[0].provider == "recording"

        publication_waiting = await service.approve(
            waiting.run_id, ApprovalRequest(decision="approve", comment="Proceed")
        )

        assert publication_waiting.stage == Stage.MERGE_APPROVAL
        assert publication_waiting.status == RunStatus.WAITING
        assert publication_waiting.pending_action["type"] == "merge_approval"
        completed = await service.resume(
            waiting.run_id, ResumeRequest(decision="approve", comment="Publish")
        )
        assert completed.stage == Stage.COMPLETED
        assert completed.status == RunStatus.COMPLETED
        assert completed.next_nodes == []
        assert worker.calls == 1
        assert provider.review_calls == 1
        assert provider.risk_calls == 1
        assert provider.supervisor_calls == 1
        assert [run.role for run in completed.model_runs] == [
            "planner", "reviewer", "risk", "supervisor"
        ]
        assert completed.supervision.decision == "approve"
        assert [item.title for item in completed.timeline] == [
            "Requirement accepted",
            "Plan created",
            "Plan approved",
            "Worker completed",
            "Review completed",
            "Risk assessed",
            "Change accepted",
            "Publication approved",
            "Published to authority",
        ]

        with pytest.raises(RunConflictError):
            await service.approve(completed.run_id, ApprovalRequest(decision="approve"))

    asyncio.run(scenario())


def test_rejected_plan_never_reaches_worker():
    async def scenario():
        service, _, worker = make_service()
        waiting = await service.start(
            StartRunRequest(project_id="shop", requirement="Delete data")
        )
        rejected = await service.approve(
            waiting.run_id, ApprovalRequest(decision="reject", comment="Unsafe")
        )

        assert rejected.stage == Stage.REJECTED
        assert rejected.status == RunStatus.REJECTED
        assert worker.calls == 0

    asyncio.run(scenario())


def test_new_graph_instance_resumes_same_checkpoint():
    async def scenario():
        checkpointer = InMemorySaver()
        first_service, _, first_worker = make_service(checkpointer)
        waiting = await first_service.start(
            StartRunRequest(project_id="shop", requirement="Resume after restart")
        )

        second_service, second_provider, second_worker = make_service(checkpointer)
        publication_waiting = await second_service.approve(
            waiting.run_id, ApprovalRequest(decision="approve")
        )
        completed = await second_service.resume(
            publication_waiting.run_id, ResumeRequest(decision="approve")
        )

        assert completed.status == RunStatus.COMPLETED
        assert first_worker.calls == 0
        assert second_worker.calls == 1
        assert second_provider.plan_calls == 0

    asyncio.run(scenario())


def test_checkpoint_history_exposes_waiting_boundary():
    async def scenario():
        service, _, _ = make_service()
        waiting = await service.start(
            StartRunRequest(project_id="shop", requirement="Inspect checkpoint history")
        )
        history = await service.history(waiting.run_id)

        assert any(item["next_nodes"] == ["plan_approval"] for item in history)
        assert any(item["stage"] == Stage.INTAKE for item in history)

    asyncio.run(scenario())


def test_worker_failure_blocks_with_reason_and_can_retry():
    async def scenario():
        provider = RecordingProvider()
        worker = RecoveringWorker()
        service = RunService(build_main_graph(provider, worker, InMemorySaver()))
        waiting = await service.start(
            StartRunRequest(project_id="shop", requirement="Recover implementation")
        )
        blocked = await service.approve(waiting.run_id, ApprovalRequest(decision="approve"))

        assert blocked.status == RunStatus.BLOCKED
        assert blocked.stage == Stage.IMPLEMENTATION_BLOCKED
        assert blocked.blocking_reason["code"] == "RuntimeError"
        assert blocked.pending_action["choices"] == ["retry", "cancel"]

        publication_waiting = await service.resume(
            blocked.run_id, ResumeRequest(decision="retry")
        )
        assert publication_waiting.stage == Stage.MERGE_APPROVAL
        completed = await service.resume(
            blocked.run_id, ResumeRequest(decision="approve")
        )
        assert completed.status == RunStatus.COMPLETED
        assert worker.calls == 2

    asyncio.run(scenario())


def test_publication_can_be_rejected_after_supervision():
    async def scenario():
        service, _, _ = make_service()
        waiting = await service.start(
            StartRunRequest(project_id="shop", requirement="Prepare but do not publish")
        )
        publication_waiting = await service.approve(
            waiting.run_id, ApprovalRequest(decision="approve")
        )
        rejected = await service.resume(
            publication_waiting.run_id, ResumeRequest(decision="reject", comment="Hold")
        )
        assert rejected.status == RunStatus.REJECTED
        assert rejected.publication is None

    asyncio.run(scenario())


def test_publication_failure_blocks_with_reason_and_can_retry():
    async def scenario():
        provider = RecordingProvider()
        worker = RecordingWorker()
        publisher = RecoveringPublisher()
        graph = build_main_graph(provider, worker, InMemorySaver(), publisher)
        service = RunService(graph)
        waiting = await service.start(
            StartRunRequest(project_id="shop", requirement="Recover publication")
        )
        publication_waiting = await service.approve(
            waiting.run_id, ApprovalRequest(decision="approve")
        )
        blocked = await service.resume(
            publication_waiting.run_id, ResumeRequest(decision="approve")
        )

        assert blocked.stage == Stage.MERGE_BLOCKED
        assert blocked.status == RunStatus.BLOCKED
        assert blocked.pending_action["type"] == "publication_recovery"
        completed = await service.resume(blocked.run_id, ResumeRequest(decision="retry"))
        assert completed.status == RunStatus.COMPLETED
        assert completed.publication.published_commit == "after"
        assert publisher.calls == 2

    asyncio.run(scenario())


def test_supervisor_rejection_automatically_returns_to_worker():
    async def scenario():
        provider = RejectOnceProvider()
        worker = RevisionRecordingWorker()
        graph = build_main_graph(provider, worker, InMemorySaver())
        service = RunService(graph)
        waiting = await service.start(
            StartRunRequest(project_id="shop", requirement="Handle a boundary")
        )

        publication_waiting = await service.approve(
            waiting.run_id, ApprovalRequest(decision="approve")
        )

        assert publication_waiting.stage == Stage.MERGE_APPROVAL
        assert publication_waiting.revision_count == 1
        assert worker.calls == 2
        assert worker.revisions[0] == (0, "")
        assert worker.revisions[1][0] == 1
        assert "boundary test" in worker.revisions[1][1]
        assert provider.review_calls == 2
        assert provider.supervisor_calls == 2

    asyncio.run(scenario())


def test_revision_limit_requires_owner_decision():
    async def scenario():
        provider = AlwaysRejectProvider()
        worker = RevisionRecordingWorker()
        graph = build_main_graph(provider, worker, InMemorySaver())
        service = RunService(graph)
        waiting = await service.start(
            StartRunRequest(project_id="shop", requirement="Bound an unsafe loop")
        )

        limited = await service.approve(
            waiting.run_id, ApprovalRequest(decision="approve")
        )

        assert limited.status == RunStatus.WAITING
        assert limited.stage == Stage.SUPERVISION
        assert limited.pending_action["type"] == "revision_limit"
        assert limited.revision_count == 2
        assert worker.calls == 3
        rejected = await service.resume(
            limited.run_id, ResumeRequest(decision="cancel", comment="Stop")
        )
        assert rejected.status == RunStatus.REJECTED

    asyncio.run(scenario())
