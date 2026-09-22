import asyncio
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from taskhub_v2.domain.models import (
    AcceptanceEvidence,
    AcceptanceResult,
    AcceptanceSubmission,
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
from taskhub_v2.persistence.task_index import MemoryTaskIndex
from taskhub_v2.providers.fallback import ProvidersExhaustedError
from taskhub_v2.services.runs import RunConflictError, RunService
from taskhub_v2.workers.git_coder import WorkerExecutionError
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


class FailureRecoveringWorker(RecordingWorker):
    def __init__(self):
        super().__init__()
        self.feedback = []

    async def execute(
        self, run_id, project_id, requirement, plan, revision=0, feedback=""
    ):
        self.calls += 1
        self.feedback.append(feedback)
        if self.calls == 1:
            raise WorkerExecutionError(
                "tests_failed",
                "Command failed (exit 1): npm run api:test\n"
                "FAILED tests/test_shop.py::test_isolation",
                diagnostics=[{
                    "command": ["npm", "run", "api:test"],
                    "exit_code": 1,
                    "output": "FAILED tests/test_shop.py::test_isolation",
                }],
            )
        return ExecutionResult(summary="tests repaired")


class TimeoutWorker(RecordingWorker):
    async def execute(self, *args, **kwargs):
        self.calls += 1
        raise WorkerExecutionError(
            "tests_timeout",
            "Command failed (exit 124): npm run check\ncommand timed out",
            diagnostics=[{
                "command": ["npm", "run", "check"],
                "exit_code": 124,
                "output": "command timed out",
            }],
        )


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


class RecoveringSupervisorProvider(RecordingProvider):
    async def supervise(self, requirement, implementation, review, risk):
        self.supervisor_calls += 1
        if self.supervisor_calls == 1:
            raise ProvidersExhaustedError(
                "supervisor",
                ["gpt_api:model_not_found (HTTP 404: requested model was not found)"],
            )
        return ModelResult(
            content=SupervisionDecision(
                decision="approve", summary="accepted", reasons=["tests pass"]
            ),
            provider="recording",
            model="test",
        )


class RecoveringRiskProvider(RecordingProvider):
    async def assess_risk(self, requirement, implementation):
        self.risk_calls += 1
        if self.risk_calls == 1:
            raise ProvidersExhaustedError("risk", ["minimax_api:ReadTimeout"])
        return ModelResult(content="low", provider="recording", model="test")


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


class RecoveringAcceptance:
    def __init__(self):
        self.calls = 0

    async def verify(self, run_id, project_id, implementation):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("browser unavailable")
        return AcceptanceResult(
            status="passed",
            evidence=[
                AcceptanceEvidence(
                    id="browser", kind="browser", status="passed",
                    source="windows-gui-34", summary="Edge passed",
                )
            ],
        )


class InvalidContractAcceptance:
    def __init__(self, reason="acceptance_contract_invalid"):
        self.reason = reason

    async def verify(self, run_id, project_id, implementation):
        error = RuntimeError("contract schema mismatch")
        error.reason = self.reason
        error.detail = "use the required contract template"
        raise error


class EvidenceAwareProvider(RecordingProvider):
    async def supervise(self, requirement, implementation, review, risk):
        self.supervisor_calls += 1
        approved = "windows-gui-34" in implementation
        return ModelResult(
            content=SupervisionDecision(
                decision="approve" if approved else "reject",
                summary="accepted" if approved else "Browser evidence required",
                reasons=["Edge evidence present" if approved else "Run Edge acceptance"],
            ),
            provider="recording",
            model="test",
        )


def make_service(checkpointer=None):
    provider = RecordingProvider()
    worker = RecordingWorker()
    graph = build_main_graph(provider, worker, checkpointer or InMemorySaver())
    return RunService(graph), provider, worker


def test_startup_recovery_replays_running_checkpoint():
    async def scenario():
        class Index:
            async def list(self, **filters):
                items = (
                    [SimpleNamespace(run_id="run-1")]
                    if filters["status"] == RunStatus.RUNNING else []
                )
                return SimpleNamespace(items=items)

        service, _, _ = make_service()
        service.task_index = Index()
        replayed = []

        async def get(run_id):
            return SimpleNamespace(
                run_id=run_id, pending_action=None, next_nodes=["implementation"],
                archived_at=None,
            )

        async def replay(run_id):
            replayed.append(run_id)

        service.get = get
        service.replay = replay
        await service.recover_interrupted()
        assert replayed == ["run-1"]

    asyncio.run(scenario())


def test_startup_recovery_advances_retired_approval_checkpoints():
    async def scenario():
        class Index:
            async def list(self, **filters):
                items = []
                if filters["status"] == RunStatus.WAITING:
                    items = [
                        SimpleNamespace(run_id="old-plan"),
                        SimpleNamespace(run_id="old-publication"),
                    ]
                return SimpleNamespace(items=items)

        service, _, _ = make_service()
        service.task_index = Index()
        advanced = []

        async def get(run_id):
            action = "plan_approval" if run_id == "old-plan" else "merge_approval"
            return SimpleNamespace(pending_action={"type": action})

        async def approve(run_id, request):
            advanced.append((run_id, request.decision))

        async def resume(run_id, request):
            advanced.append((run_id, request.decision))

        service.get = get
        service.approve = approve
        service.resume = resume
        await service.recover_interrupted()
        assert advanced == [("old-plan", "approve"), ("old-publication", "approve")]

    asyncio.run(scenario())


def test_run_automatically_implements_and_publishes():
    async def scenario():
        service, provider, worker = make_service()

        completed = await service.start(
            StartRunRequest(project_id="shop", requirement="Add product search")
        )

        assert completed.stage == Stage.COMPLETED
        assert completed.status == RunStatus.COMPLETED
        assert completed.next_nodes == []
        assert completed.pending_action is None
        assert provider.plan_calls == 1
        assert worker.calls == 1
        assert completed.model_runs[0].role == "planner"
        assert completed.model_runs[0].provider == "recording"
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
            "Worker completed",
            "Acceptance evidence collected",
            "Review completed",
            "Risk assessed",
            "Change accepted",
            "Published to authority",
        ]

        with pytest.raises(RunConflictError):
            await service.approve(completed.run_id, ApprovalRequest(decision="approve"))

    asyncio.run(scenario())


def test_obsolete_plan_approval_cannot_interrupt_completed_run():
    async def scenario():
        service, _, worker = make_service()
        completed = await service.start(
            StartRunRequest(project_id="shop", requirement="Delete data")
        )
        with pytest.raises(RunConflictError):
            await service.approve(
                completed.run_id, ApprovalRequest(decision="reject", comment="Unsafe")
            )
        assert worker.calls == 1

    asyncio.run(scenario())


def test_implementation_block_preserves_failed_coder_runs():
    class DiagnosticWorker(RecordingWorker):
        async def execute(self, *args, **kwargs):
            self.calls += 1
            error = RuntimeError("coding completed twice without file changes")
            error.reason = "no_changes"
            error.detail = "first summary; second summary"
            error.model_results = [
                ModelResult(content="first", provider="plus", model="gpt-5", duration_ms=10),
                ModelResult(content="second", provider="pro", model="gpt-5", duration_ms=20),
            ]
            raise error

    async def scenario():
        provider = RecordingProvider()
        worker = DiagnosticWorker()
        service = RunService(build_main_graph(provider, worker, InMemorySaver()))
        blocked = await service.start(
            StartRunRequest(project_id="shop", requirement="Implement shop model")
        )
        assert blocked.stage == Stage.IMPLEMENTATION_BLOCKED
        assert blocked.blocking_reason["code"] == "no_changes"
        assert blocked.blocking_reason["detail"] == "first summary; second summary"
        assert blocked.blocking_reason["attempts"] == [
            {
                "provider": "plus", "model": "gpt-5", "duration_ms": 10,
                "summary": "first", "failed_providers": [],
            },
            {
                "provider": "pro", "model": "gpt-5", "duration_ms": 20,
                "summary": "second", "failed_providers": [],
            },
        ]
        assert [item.provider for item in blocked.model_runs[-2:]] == ["plus", "pro"]
        assert [item.duration_ms for item in blocked.model_runs[-2:]] == [10, 20]

    asyncio.run(scenario())


def test_test_failure_automatically_returns_diagnostics_to_coder():
    async def scenario():
        provider = RecordingProvider()
        worker = FailureRecoveringWorker()
        service = RunService(build_main_graph(provider, worker, InMemorySaver()))
        completed = await service.start(
            StartRunRequest(project_id="shop", requirement="Implement shop isolation")
        )

        assert completed.stage == Stage.COMPLETED
        assert completed.revision_count == 1
        assert worker.calls == 2
        assert "npm run api:test" in worker.feedback[1]
        assert "test_isolation" in worker.feedback[1]
        assert any(
            item.title == "Automatic test-failure revision 1 started"
            for item in completed.timeline
        )

    asyncio.run(scenario())


def test_test_timeout_waits_for_infrastructure_recovery_without_code_revision():
    async def scenario():
        worker = TimeoutWorker()
        service = RunService(
            build_main_graph(RecordingProvider(), worker, InMemorySaver())
        )

        blocked = await service.start(
            StartRunRequest(project_id="shop", requirement="Run a long quality suite")
        )

        assert blocked.stage == Stage.IMPLEMENTATION_BLOCKED
        assert blocked.status == RunStatus.BLOCKED
        assert blocked.blocking_reason["code"] == "tests_timeout"
        assert blocked.pending_action["type"] == "implementation_recovery"
        assert blocked.pending_action["choices"] == ["retry", "cancel"]
        assert blocked.revision_count == 0
        assert worker.calls == 1

    asyncio.run(scenario())


def test_new_graph_instance_resumes_same_checkpoint():
    async def scenario():
        checkpointer = InMemorySaver()
        first_service, _, first_worker = make_service(checkpointer)
        completed = await first_service.start(
            StartRunRequest(project_id="shop", requirement="Resume after restart")
        )

        second_service, second_provider, second_worker = make_service(checkpointer)
        restored = await second_service.get(completed.run_id)

        assert restored.status == RunStatus.COMPLETED
        assert first_worker.calls == 1
        assert second_worker.calls == 0
        assert second_provider.plan_calls == 0

    asyncio.run(scenario())


def test_checkpoint_history_exposes_automatic_boundary():
    async def scenario():
        service, _, _ = make_service()
        completed = await service.start(
            StartRunRequest(project_id="shop", requirement="Inspect checkpoint history")
        )
        history = await service.history(completed.run_id)

        assert not any(item["next_nodes"] == ["plan_approval"] for item in history)
        assert any(item["next_nodes"] == ["implementation"] for item in history)
        assert any(item["stage"] == Stage.INTAKE for item in history)

    asyncio.run(scenario())


def test_worker_failure_blocks_with_reason_and_can_retry():
    async def scenario():
        provider = RecordingProvider()
        worker = RecoveringWorker()
        service = RunService(build_main_graph(provider, worker, InMemorySaver()))
        blocked = await service.start(
            StartRunRequest(project_id="shop", requirement="Recover implementation")
        )

        assert blocked.status == RunStatus.BLOCKED
        assert blocked.stage == Stage.IMPLEMENTATION_BLOCKED
        assert blocked.blocking_reason["code"] == "RuntimeError"
        assert blocked.pending_action["choices"] == ["retry", "cancel"]

        completed = await service.resume(
            blocked.run_id, ResumeRequest(decision="retry")
        )
        assert completed.stage == Stage.COMPLETED
        assert completed.status == RunStatus.COMPLETED
        assert worker.calls == 2

    asyncio.run(scenario())


def test_obsolete_publication_decision_cannot_interrupt_completed_run():
    async def scenario():
        service, _, _ = make_service()
        completed = await service.start(
            StartRunRequest(project_id="shop", requirement="Prepare but do not publish")
        )
        with pytest.raises(RunConflictError):
            await service.resume(
                completed.run_id, ResumeRequest(decision="reject", comment="Hold")
            )
        assert completed.publication is not None

    asyncio.run(scenario())


def test_publication_failure_blocks_with_reason_and_can_retry():
    async def scenario():
        provider = RecordingProvider()
        worker = RecordingWorker()
        publisher = RecoveringPublisher()
        graph = build_main_graph(provider, worker, InMemorySaver(), publisher)
        service = RunService(graph)
        blocked = await service.start(
            StartRunRequest(project_id="shop", requirement="Recover publication")
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
        completed = await service.start(
            StartRunRequest(project_id="shop", requirement="Handle a boundary")
        )
        assert completed.stage == Stage.COMPLETED
        assert completed.revision_count == 1
        assert worker.calls == 2
        assert worker.revisions[0] == (0, "")
        assert worker.revisions[1][0] == 1
        assert "boundary test" in worker.revisions[1][1]
        assert provider.review_calls == 2
        assert provider.supervisor_calls == 2

    asyncio.run(scenario())


def test_supervisor_model_exhaustion_blocks_and_retries_only_supervision():
    async def scenario():
        provider = RecoveringSupervisorProvider()
        worker = RecordingWorker()
        service = RunService(build_main_graph(provider, worker, InMemorySaver()))
        blocked = await service.start(
            StartRunRequest(project_id="shop", requirement="Preserve acceptance evidence")
        )

        assert blocked.status == RunStatus.BLOCKED
        assert blocked.stage == Stage.SUPERVISION
        assert blocked.next_nodes == ["supervision_recovery"]
        assert blocked.pending_action["type"] == "supervision_recovery"
        assert blocked.pending_action["choices"] == ["retry", "cancel"]
        assert blocked.blocking_reason["code"] == "model_resources_unavailable"
        assert "HTTP 404" in blocked.blocking_reason["detail"]
        implementation = blocked.implementation
        acceptance = blocked.acceptance

        completed = await service.resume(
            blocked.run_id, ResumeRequest(decision="retry")
        )
        assert completed.stage == Stage.COMPLETED
        assert completed.implementation == implementation
        assert completed.acceptance == acceptance
        assert worker.calls == 1
        assert provider.review_calls == 1
        assert provider.risk_calls == 1
        assert provider.supervisor_calls == 2

    asyncio.run(scenario())


def test_risk_model_exhaustion_blocks_and_retries_only_risk():
    async def scenario():
        provider = RecoveringRiskProvider()
        worker = RecordingWorker()
        service = RunService(build_main_graph(provider, worker, InMemorySaver()))
        blocked = await service.start(
            StartRunRequest(project_id="shop", requirement="Preserve acceptance evidence")
        )

        assert blocked.status == RunStatus.BLOCKED
        assert blocked.stage == Stage.RISK
        assert blocked.next_nodes == ["risk_recovery"]
        assert blocked.pending_action["type"] == "risk_recovery"
        assert blocked.blocking_reason["code"] == "model_resources_unavailable"

        completed = await service.resume(
            blocked.run_id, ResumeRequest(decision="retry")
        )
        assert completed.stage == Stage.COMPLETED
        assert worker.calls == 1
        assert provider.review_calls == 1
        assert provider.risk_calls == 2
        assert provider.supervisor_calls == 1

    asyncio.run(scenario())


def test_revision_limit_requires_owner_decision():
    async def scenario():
        provider = AlwaysRejectProvider()
        worker = RevisionRecordingWorker()
        graph = build_main_graph(provider, worker, InMemorySaver())
        service = RunService(graph)
        limited = await service.start(
            StartRunRequest(project_id="shop", requirement="Bound an unsafe loop")
        )

        assert limited.status == RunStatus.WAITING
        assert limited.stage == Stage.SUPERVISION
        assert limited.pending_action["type"] == "revision_limit"
        assert "manual" in limited.pending_action["choices"]
        assert limited.revision_count == 2
        assert worker.calls == 3
        manual = await service.resume(
            limited.run_id, ResumeRequest(decision="manual", comment="Bootstrap a node")
        )
        assert manual.status == RunStatus.WAITING
        assert manual.stage == Stage.SUPERVISION
        assert manual.pending_action["type"] == "manual_intervention"
        assert "Do not modify the managed-project" in manual.pending_action["description"]
        assert manual.next_nodes == ["revision_limit"]
        assert worker.calls == 3
        rejected = await service.resume(
            manual.run_id, ResumeRequest(decision="cancel", comment="Stop")
        )
        assert rejected.status == RunStatus.REJECTED

    asyncio.run(scenario())


def test_acceptance_failure_has_its_own_recovery_without_rerunning_worker():
    async def scenario():
        provider = RecordingProvider()
        worker = RecordingWorker()
        acceptance = RecoveringAcceptance()
        service = RunService(
            build_main_graph(
                provider, worker, InMemorySaver(), acceptance=acceptance
            )
        )
        blocked = await service.start(
            StartRunRequest(project_id="shop", requirement="Verify in Edge")
        )
        assert blocked.stage == Stage.ACCEPTANCE_BLOCKED
        assert blocked.pending_action["type"] == "acceptance_recovery"

        completed = await service.resume(
            blocked.run_id, ResumeRequest(decision="retry")
        )
        assert completed.stage == Stage.COMPLETED
        assert acceptance.calls == 2
        assert worker.calls == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "reason", ["acceptance_contract_invalid", "acceptance_suite_invalid"]
)
def test_invalid_acceptance_contract_only_returns_to_implementation(reason):
    async def scenario():
        service = RunService(build_main_graph(
            RecordingProvider(), RecordingWorker(), InMemorySaver(),
            acceptance=InvalidContractAcceptance(reason),
        ))
        blocked = await service.start(StartRunRequest(
            project_id="shop", requirement="Create browser acceptance contract"
        ))

        assert blocked.stage == Stage.ACCEPTANCE_BLOCKED
        assert blocked.blocking_reason["code"] == reason
        assert blocked.blocking_reason["responsible_node"] == "implementation"
        assert blocked.pending_action["choices"] == ["revise", "cancel"]

    asyncio.run(scenario())


def test_acceptance_failure_can_return_to_worker_from_legacy_checkpoint_at_limit():
    async def scenario():
        provider = RecordingProvider()
        worker = RevisionRecordingWorker()
        acceptance = RecoveringAcceptance()
        checkpointer = InMemorySaver()
        graph = build_main_graph(
            provider, worker, checkpointer, acceptance=acceptance
        )
        service = RunService(graph, task_index=MemoryTaskIndex())
        blocked = await service.start(
            StartRunRequest(project_id="shop", requirement="Repair acceptance defect")
        )
        assert blocked.stage == Stage.ACCEPTANCE_BLOCKED
        assert blocked.pending_action["choices"] == ["retry", "revise", "cancel"]

        # Reproduce a pre-upgrade checkpoint which has reached its revision limit
        # and does not yet advertise the new decision.
        await graph.aupdate_state(
            service._config(blocked.run_id),
            {
                "revision_count": 2,
                "max_revision_attempts": 2,
                "pending_action": {
                    "type": "acceptance_recovery",
                    "title": "Acceptance needs attention",
                    "choices": ["retry", "cancel"],
                },
            },
        )
        completed = await service.resume(
            blocked.run_id,
            ResumeRequest(decision="revise", comment="Approve one extra revision"),
        )

        assert completed.stage == Stage.COMPLETED
        assert completed.revision_count == 3
        assert completed.max_revision_attempts == 3
        assert worker.calls == 2
        assert worker.revisions[-1][0] == 3
        assert "RuntimeError: browser unavailable" in worker.revisions[-1][1]
        assert acceptance.calls == 2
        assert any(
            item.title == "Acceptance returned to implementation"
            for item in completed.timeline
        )
        assert any(
            item.title == "Acceptance revision 3 started"
            for item in completed.timeline
        )

    asyncio.run(scenario())


def test_submitted_acceptance_evidence_reassesses_without_coding_revision():
    async def scenario():
        provider = EvidenceAwareProvider()
        worker = RecordingWorker()
        service = RunService(build_main_graph(provider, worker, InMemorySaver()))
        limited = await service.start(
            StartRunRequest(project_id="shop", requirement="Require browser evidence")
        )
        # Two automatic revisions are expected before the configured limit.
        calls_at_limit = worker.calls
        assert limited.pending_action["type"] == "revision_limit"

        reassessed = await service.submit_acceptance(
            limited.run_id,
            AcceptanceSubmission(
                evidence=[
                    AcceptanceEvidence(
                        id="edge",
                        kind="browser",
                        status="passed",
                        source="windows-gui-34",
                        summary="Microsoft Edge workflow passed",
                    )
                ]
            ),
        )
        assert reassessed.stage == Stage.COMPLETED
        assert reassessed.acceptance.evidence[-1].source == "windows-gui-34"
        assert worker.calls == calls_at_limit

    asyncio.run(scenario())


@pytest.mark.parametrize("offline", [False, True])
def test_structured_browser_rejection_never_consumes_revision(tmp_path, offline):
    from taskhub_v2.domain.models import Workspace

    (tmp_path / ".taskhub").mkdir()
    (tmp_path / ".taskhub/acceptance.yaml").write_text("workload: browser_acceptance")

    class Worker(RecordingWorker):
        async def execute(self, *args, **kwargs):
            self.calls += 1
            return ExecutionResult(summary="candidate", workspace=Workspace(
                project_id="shop", path=str(tmp_path), branch="candidate", base_commit="a" * 40
            ))

    class Provider(RecordingProvider):
        async def supervise(self, requirement, implementation, review, risk):
            self.supervisor_calls += 1
            first = self.supervisor_calls == 1
            return ModelResult(content=SupervisionDecision(
                decision="reject" if first else "approve", summary="browser evidence",
                reasons=[], missing_evidence=["browser"] if first else []
            ), provider="recording", model="test")

    class Acceptance:
        calls = 0
        async def verify(self, *args):
            self.calls += 1
            if self.calls == 1:
                return AcceptanceResult(status="passed", evidence=[])
            if offline:
                raise RuntimeError("Windows 验收节点离线")
            return AcceptanceResult(status="passed", evidence=[AcceptanceEvidence(
                id="browser", kind="browser", status="passed", source="windows-gui-34",
                summary="test fixture evidence"
            )])

    async def scenario():
        worker, provider, acceptance = Worker(), Provider(), Acceptance()
        service = RunService(build_main_graph(provider, worker, InMemorySaver(),
                                              acceptance=acceptance))
        result = await service.start(StartRunRequest(project_id="shop", requirement="Browser flow"))
        assert result.revision_count == 0
        assert worker.calls == 1
        assert acceptance.calls == 2
        if offline:
            assert result.status == RunStatus.BLOCKED
            assert result.blocking_reason["detail"] == "Windows 验收节点离线"
        else:
            assert result.stage == Stage.COMPLETED
            assert provider.supervisor_calls == 2
    asyncio.run(scenario())


def test_non_browser_evidence_gap_does_not_consume_coding_revision():
    class Provider(RecordingProvider):
        async def supervise(self, requirement, implementation, review, risk):
            self.supervisor_calls += 1
            return ModelResult(content=SupervisionDecision(
                decision="reject",
                summary="database rehearsal evidence required",
                reasons=["submit restored PostgreSQL counts"],
                missing_evidence=["database"],
            ), provider="recording", model="test")

    async def scenario():
        provider = Provider()
        worker = RecordingWorker()
        service = RunService(build_main_graph(provider, worker, InMemorySaver()))
        result = await service.start(StartRunRequest(
            project_id="shop", requirement="Preserve production data counts"
        ))

        assert result.stage == Stage.SUPERVISION
        assert result.status == RunStatus.WAITING
        assert result.pending_action["type"] == "revision_limit"
        assert result.supervision.missing_evidence == ["database"]
        assert result.revision_count == 0
        assert worker.calls == 1

        recollected = await service.resume(
            result.run_id, ResumeRequest(decision="recheck")
        )
        assert recollected.stage == Stage.SUPERVISION
        assert recollected.status == RunStatus.WAITING
        assert recollected.revision_count == 0
        assert worker.calls == 1
        assert provider.supervisor_calls == 2
        assert any(
            item.title == "Acceptance evidence recollection requested"
            for item in recollected.timeline
        )

    asyncio.run(scenario())
