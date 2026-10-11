import asyncio

import pytest

from taskhub_v2.domain.dag import ExecutionBatch
from taskhub_v2.domain.models import (
    ExecutionResult,
    Plan,
    ProjectDefinition,
    ScheduledTests,
    Workspace,
)
from taskhub_v2.domain.models import TestExecution as CommandExecution
from taskhub_v2.domain.production import (
    ExecutionPlan,
    ExecutionPlanStatus,
    ProductionTask,
    ProductionTaskStatus,
    ProductSpec,
    TaskAttempt,
    TaskAttemptStatus,
)
from taskhub_v2.domain.project_contract import (
    ArtifactContract,
    ContractCommands,
    ModuleContract,
    ProjectContract,
)
from taskhub_v2.persistence.production import MemoryProductionStore
from taskhub_v2.services.dag_plan import DagPlanService
from taskhub_v2.services.dag_runtime import ProductizedExecutionRuntime
from taskhub_v2.services.dag_scheduler import PersistentDagScheduler
from taskhub_v2.services.fair_budget import FairProjectBudget
from taskhub_v2.workers.dag_executor import WorkerDagExecutor


def contract() -> ProjectContract:
    return ProjectContract(
        project_id="demo",
        contract_id="pc_demo",
        version=1,
        status="active",
        profile_id="python-service",
        languages=["python"],
        modules=[
            ModuleContract(name="api", paths=["src/api/**"]),
            ModuleContract(name="domain", paths=["src/domain/**"]),
        ],
        commands=ContractCommands(test=[["python3", "-m", "pytest", "-q"]]),
    )


def specification() -> ProductSpec:
    return ProductSpec(
        project_id="demo",
        spec_id="ps_demo",
        version=1,
        status="approved",
        goals=["交付可验证服务"],
        acceptance_criteria=["自动测试通过"],
    )


class RecordingExecutor:
    def __init__(self, fail_once=False):
        self.calls = []
        self.active = 0
        self.max_active = 0
        self.fail_once = fail_once

    async def execute(self, task, attempt, *, base_commit):
        self.calls.append((task.task_id, attempt.attempt_number))
        node_id = f"node-{len(self.calls)}"
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.02)
        self.active -= 1
        if self.fail_once and len(self.calls) == 1:
            raise RuntimeError("node disconnected")
        return ExecutionResult(
            summary=f"completed {task.task_id}",
            coding_node=node_id,
        )


async def compiled(store):
    spec = await store.save(specification())
    approved_contract = await store.save(contract())
    service = DagPlanService(store)
    return await service.compile(
        "demo",
        "run-parallel",
        spec,
        approved_contract,
        Plan(
            summary="parallel plan",
            steps=["Implement API", "Implement domain", "Verify integration"],
            acceptance=["tests pass"],
        ),
    )


def test_plan_compiles_validated_dag_and_persists_exact_sources():
    async def scenario():
        store = MemoryProductionStore()
        bundle = await compiled(store)
        assert bundle.plan.status == ExecutionPlanStatus.ACTIVE
        assert bundle.plan.product_spec_id == "ps_demo"
        assert bundle.plan.project_contract_id == "pc_demo"
        assert len(bundle.tasks) == 3
        assert bundle.tasks[2].depends_on == [
            bundle.tasks[0].task_id,
            bundle.tasks[1].task_id,
        ]
        assert "coding" in bundle.tasks[0].required_capabilities
        assert "coding" not in bundle.tasks[2].required_capabilities
        assert bundle.topological_order[-1] == bundle.tasks[2].task_id
        restored = await DagPlanService(store).for_run("demo", "run-parallel")
        assert restored and restored.plan == bundle.plan

    asyncio.run(scenario())


def test_plan_selects_paths_by_task_intent_and_reserves_artifacts_for_verification():
    async def scenario():
        store = MemoryProductionStore()
        checked_contract = contract().model_copy(
            update={
                "modules": [
                    ModuleContract(name="web", paths=["apps/web/src/**"]),
                    ModuleContract(name="api", paths=["apps/api/app/**"]),
                    ModuleContract(name="documentation", paths=["docs/**"]),
                ],
                "artifacts": ArtifactContract(required_artifacts=["dist/**"]),
            }
        )
        bundle = await DagPlanService(store).compile(
            "demo",
            "run-intent-paths",
            specification(),
            checked_contract,
            Plan(
                summary="component convergence",
                steps=[
                    "Implement UI components and responsive page states",
                    "Update docs/ARCHITECTURE.md and ADR records",
                    "Review final changes and audit generated artifacts",
                ],
                acceptance=["checks pass"],
            ),
        )

        assert bundle.tasks[0].allowed_paths == ["apps/web/src/**"]
        assert bundle.tasks[0].expected_artifacts == []
        assert bundle.tasks[1].allowed_paths == ["docs/**"]
        assert bundle.tasks[2].task_type == "verification"
        assert bundle.tasks[2].allowed_paths == []
        assert bundle.tasks[2].expected_artifacts == ["dist/**"]

    asyncio.run(scenario())


def test_plan_preserves_preflight_order_and_does_not_misclassify_audited_service_work():
    async def scenario():
        store = MemoryProductionStore()
        checked_contract = contract().model_copy(
            update={
                "modules": [
                    ModuleContract(name="web", paths=["apps/web/src/**"]),
                    ModuleContract(name="api", paths=["apps/api/app/**"]),
                ]
            }
        )
        bundle = await DagPlanService(store).compile(
            "demo",
            "run-supply-plan",
            specification(),
            checked_contract,
            Plan(
                summary="supply delivery",
                steps=[
                    "前置确认：核对项目契约和质量命令",
                    "领域模型与数据库迁移：实现供应链状态机",
                    "服务端流程：实现审批、审计和异常恢复",
                    "最终验证：运行 API 与回归测试",
                ],
                acceptance=["checks pass"],
            ),
        )

        preflight, migration, service, final = bundle.tasks
        assert preflight.task_type == "verification"
        assert preflight.depends_on == []
        assert migration.task_type == "implementation"
        assert migration.depends_on == [preflight.task_id]
        assert migration.allowed_paths == ["apps/api/app/**"]
        assert migration.estimated_size == "medium"
        assert service.task_type == "implementation"
        assert service.depends_on == [preflight.task_id]
        assert final.depends_on == [migration.task_id, service.task_id, preflight.task_id]

    asyncio.run(scenario())


def test_plan_compilation_resumes_after_partial_persistence():
    class InterruptingStore(MemoryProductionStore):
        def __init__(self):
            super().__init__()
            self.task_saves = 0
            self.interrupted = False

        async def save(self, record):
            if isinstance(record, ProductionTask):
                self.task_saves += 1
                if self.task_saves == 2 and not self.interrupted:
                    self.interrupted = True
                    raise RuntimeError("controller stopped")
            return await super().save(record)

    async def scenario():
        store = InterruptingStore()
        service = DagPlanService(store)
        plan = Plan(
            summary="resumable",
            steps=["Implement API", "Implement domain", "Verify integration"],
            acceptance=["tests pass"],
        )
        with pytest.raises(RuntimeError, match="controller stopped"):
            await service.compile("demo", "run-resume-plan", specification(), contract(), plan)
        recovered = await service.compile(
            "demo", "run-resume-plan", specification(), contract(), plan
        )
        assert recovered.plan.status == ExecutionPlanStatus.ACTIVE
        assert len(recovered.tasks) == 3

    asyncio.run(scenario())


def test_verification_task_runs_on_test_scheduler_without_calling_coder(tmp_path):
    class Projects:
        def get(self, _project_id):
            return ProjectDefinition(id="demo", repository=str(tmp_path), test_timeout_seconds=30)

    class Workspaces:
        async def prepare(self, project, run_id, base_commit=""):
            return Workspace(
                project_id=project.id,
                path=str(tmp_path),
                branch=f"taskhub/{run_id}",
                base_commit=base_commit,
            )

    class Tests:
        async def run(self, *args, **kwargs):
            assert kwargs["workload"] == "test"
            assert kwargs["required_capabilities_override"] == {"python3", "pytest"}
            assert kwargs["eligible_node_ids"] == {"test-node"}
            assert kwargs["setup_commands"] == []
            await kwargs["on_assigned"]("test-node")
            return ScheduledTests(
                node_id="test-node",
                tests=[CommandExecution(command=["pytest"], exit_code=0, output_tail="passed")],
            )

    class Coder:
        async def execute(self, *args, **kwargs):
            raise AssertionError("verification task must not invoke the coding worker")

    async def scenario():
        async def topology_resolver(project_id, workload):
            assert (project_id, workload) == ("demo", "test")
            return {"test-node"}

        task = ProductionTask(
            project_id="demo",
            task_id="task_verify_only",
            plan_id="plan_verify",
            plan_version=1,
            title="verify",
            objective="verify",
            task_type="verification",
            required_capabilities=["python3", "pytest"],
            acceptance_commands=[["pytest"]],
        )
        attempt = TaskAttempt(
            project_id="demo",
            attempt_id="attempt_verify_01",
            task_id=task.task_id,
            attempt_number=1,
        )
        store = MemoryProductionStore()
        executor = WorkerDagExecutor(
            Coder(),
            store,
            Projects(),
            workspaces=Workspaces(),
            test_scheduler=Tests(),
            topology_resolver=topology_resolver,
        )
        result = await executor.execute(task, attempt, base_commit="abc1234")
        assert result.execution_node == "test-node"
        assert result.tests[0].exit_code == 0
        assigned_task = await store.get("task", task.task_id, "1")
        assigned_attempt = await store.get("task_attempt", attempt.attempt_id, "1")
        assert assigned_task.assigned_node_id == "test-node"
        assert assigned_attempt.node_id == "test-node"

    asyncio.run(scenario())


def test_plan_validation_rejects_cycle_missing_evidence_dataflow_and_capability():
    async def scenario():
        async def capabilities():
            return {"python3"}

        service = DagPlanService(MemoryProductionStore(), capabilities)
        tasks = [
            ProductionTask(
                project_id="demo",
                task_id="task_invalid_a",
                plan_id="plan_invalid",
                plan_version=1,
                title="all work",
                objective="complete everything",
                depends_on=["task_invalid_b"],
                inputs=[{"source_task_id": "task_invalid_b", "source_output": "missing"}],
                outputs=[{"id": "unused", "kind": "intermediate"}],
                required_capabilities=["docker"],
                estimated_size="large",
                independently_verifiable=False,
            ),
            ProductionTask(
                project_id="demo",
                task_id="task_invalid_b",
                plan_id="plan_invalid",
                plan_version=1,
                title="second",
                objective="second",
                depends_on=["task_invalid_a", "task_unknown"],
                acceptance_commands=[["test"]],
            ),
        ]
        findings, order = await service.validate(tasks, contract())
        assert order == []
        assert {
            "unknown_dependency",
            "acceptance_missing",
            "task_too_large",
            "dependency_cycle",
            "input_source_missing",
            "output_unconsumed",
            "capability_unavailable",
        }.issubset({item.code for item in findings})

    asyncio.run(scenario())


def test_scheduler_runs_independent_tasks_in_parallel_then_dependency():
    async def scenario():
        store = MemoryProductionStore()
        bundle = await compiled(store)
        executor = RecordingExecutor()
        outcome = await PersistentDagScheduler(
            store, executor, global_concurrency=2, project_concurrency=2
        ).execute(bundle.plan.plan_id)
        assert executor.max_active == 2
        assert len(outcome.batches) == 2
        assert len(outcome.batches[0].task_ids) == 2
        assert len(set(outcome.batches[0].assignments.values())) == 2
        assert outcome.batches[1].task_ids == [bundle.tasks[2].task_id]
        assert outcome.snapshot.status == "completed"
        assert all(item.status == ProductionTaskStatus.COMPLETED for item in outcome.tasks)
        assert all(item.status == TaskAttemptStatus.VALIDATED for item in outcome.attempts)

    asyncio.run(scenario())


def test_scheduler_records_governance_gate_for_every_completed_batch():
    class GovernedExecutor(RecordingExecutor):
        def __init__(self):
            super().__init__()
            self.verified = []

        async def verify_batch(self, plan, *, base_commit):
            self.verified.append((plan.plan_id, base_commit))
            return {"status": "passed", "passed": 7, "failed": []}

    async def scenario():
        store = MemoryProductionStore()
        bundle = await compiled(store)
        executor = GovernedExecutor()
        outcome = await PersistentDagScheduler(store, executor).execute(bundle.plan.plan_id)
        assert len(executor.verified) == len(outcome.batches) == 2
        assert all(batch.governance_gate["status"] == "passed" for batch in outcome.batches)

    asyncio.run(scenario())


def test_scheduler_persists_failed_governance_gate_and_blocks_batch_tasks():
    class RejectingExecutor(RecordingExecutor):
        async def verify_batch(self, plan, *, base_commit):
            return {
                "status": "failed",
                "failed": [{"summary": "src/api.py 超过文件上限"}],
            }

    async def scenario():
        store = MemoryProductionStore()
        bundle = await compiled(store)
        scheduler = PersistentDagScheduler(
            store, RejectingExecutor(), global_concurrency=2, project_concurrency=2
        )
        with pytest.raises(Exception, match="批次全局工程规则门禁未通过") as caught:
            await scheduler.execute(bundle.plan.plan_id)
        snapshot = caught.value.snapshot
        assert snapshot.status == "blocked"
        batch = await store.get("execution_batch", snapshot.detail["batch_ids"][0], "1")
        assert batch.status == "failed"
        assert batch.governance_gate["status"] == "failed"
        tasks = await store.list(project_id="demo", object_type="task")
        selected = [task for task in tasks if task.task_id in batch.task_ids]
        assert all(task.status == ProductionTaskStatus.BLOCKED for task in selected)

    asyncio.run(scenario())


def test_resource_conflicts_are_serialized_and_failure_creates_new_attempt():
    async def scenario():
        store = MemoryProductionStore()
        plan = await store.save(
            ExecutionPlan(
                project_id="demo",
                plan_id="plan_conflict",
                version=1,
                status="active",
                product_spec_id="ps_demo",
                product_spec_version=1,
                task_ids=["task_conflict_a", "task_conflict_b"],
                policy={"project_concurrency": 2},
            )
        )
        for name in ("a", "b"):
            await store.save(
                ProductionTask(
                    project_id="demo",
                    task_id=f"task_conflict_{name}",
                    plan_id=plan.plan_id,
                    plan_version=1,
                    title=name,
                    objective=name,
                    acceptance_commands=[["test"]],
                    resource_locks=["path:src/shared"],
                )
            )
        executor = RecordingExecutor(fail_once=True)
        outcome = await PersistentDagScheduler(store, executor).execute(plan.plan_id)
        assert executor.max_active == 1
        assert len(outcome.batches) == 2
        assert outcome.batches[0].waiting_reasons["task_conflict_b"] == [
            "等待资源锁：path:src/shared"
        ]
        assert (
            "健康、能力、槽位与优先级匹配"
            in outcome.batches[0].selection_reasons["task_conflict_a"]
        )
        first_attempts = [item for item in outcome.attempts if item.task_id == "task_conflict_a"]
        assert [item.status for item in first_attempts] == [
            TaskAttemptStatus.FAILED,
            TaskAttemptStatus.VALIDATED,
        ]

    asyncio.run(scenario())


def test_scheduler_does_not_repeat_an_identical_timed_out_quality_attempt():
    class QualityTimeout(RuntimeError):
        reason = "tests_timeout"

    class TimeoutExecutor(RecordingExecutor):
        async def execute(self, task, attempt, *, base_commit):
            self.calls.append((task.task_id, attempt.attempt_number))
            raise QualityTimeout("quality command timed out")

    async def scenario():
        store = MemoryProductionStore()
        bundle = await compiled(store)
        executor = TimeoutExecutor()
        with pytest.raises(Exception, match="quality command timed out"):
            await PersistentDagScheduler(store, executor).execute(bundle.plan.plan_id)
        attempts = await store.list(project_id="demo", object_type="task_attempt")
        assert executor.calls == [(bundle.tasks[0].task_id, 1), (bundle.tasks[1].task_id, 1)]
        assert {item.status for item in attempts} == {TaskAttemptStatus.TIMED_OUT}

    asyncio.run(scenario())


def test_cancelling_a_run_terminalizes_its_live_production_records():
    async def scenario():
        store = MemoryProductionStore()
        bundle = await compiled(store)
        running = await store.save(
            bundle.tasks[0].model_copy(update={"status": ProductionTaskStatus.READY})
        )
        running = await store.save(
            running.model_copy(update={"status": ProductionTaskStatus.ASSIGNED})
        )
        running = await store.save(
            running.model_copy(update={"status": ProductionTaskStatus.RUNNING})
        )
        await store.save(
            TaskAttempt(
                project_id="demo",
                attempt_id="attempt_cancel_01",
                task_id=running.task_id,
                attempt_number=1,
                plan_id=bundle.plan.plan_id,
                plan_version=1,
                status=TaskAttemptStatus.RUNNING,
            )
        )
        await store.save(
            ExecutionBatch(
                project_id="demo",
                batch_id="batch_cancel_001",
                plan_id=bundle.plan.plan_id,
                plan_version=1,
                sequence=1,
                status="running",
                task_ids=[running.task_id],
            )
        )
        runtime = ProductizedExecutionRuntime(
            planner=DagPlanService(store), scheduler=None, store=store
        )

        await runtime.cancel("demo", "run-parallel")
        view = await runtime.view("demo", "run-parallel")

        assert view["execution_plan"].status == ExecutionPlanStatus.SUPERSEDED
        assert all(task.status == ProductionTaskStatus.CANCELLED for task in view["tasks"])
        assert view["attempts"][0].status == TaskAttemptStatus.ABANDONED
        assert view["batches"][0].status == "failed"
        assert view["latest_snapshot"].status == "blocked"

    asyncio.run(scenario())


def test_restart_reuses_persisted_successful_result_without_running_command_again():
    async def scenario():
        store = MemoryProductionStore()
        plan = await store.save(
            ExecutionPlan(
                project_id="demo",
                plan_id="plan_resume",
                version=1,
                status="active",
                product_spec_id="ps_demo",
                product_spec_version=1,
                task_ids=["task_resume_one"],
            )
        )
        task = await store.save(
            ProductionTask(
                project_id="demo",
                task_id="task_resume_one",
                plan_id=plan.plan_id,
                plan_version=1,
                title="resume",
                objective="resume",
                status="verifying",
                acceptance_commands=[["test"]],
            )
        )
        await store.save(
            TaskAttempt(
                project_id="demo",
                attempt_id="attempt_resume_one_01",
                task_id=task.task_id,
                attempt_number=1,
                plan_id=plan.plan_id,
                plan_version=1,
                status="result_received",
                result=ExecutionResult(summary="already done", coding_node="node-a").model_dump(
                    mode="json"
                ),
            )
        )
        executor = RecordingExecutor()
        outcome = await PersistentDagScheduler(store, executor).execute(plan.plan_id)
        assert executor.calls == []
        assert outcome.tasks[0].status == ProductionTaskStatus.COMPLETED

    asyncio.run(scenario())


def test_global_budget_rotates_waiting_projects_fairly():
    async def scenario():
        budget = FairProjectBudget(global_limit=1, default_project_limit=1)
        order = []
        release = asyncio.Event()

        async def work(project, name, hold=False):
            async with budget.slot(project):
                order.append(name)
                if hold:
                    await release.wait()

        first = asyncio.create_task(work("a", "a1", True))
        await asyncio.sleep(0)
        second_a = asyncio.create_task(work("a", "a2"))
        first_b = asyncio.create_task(work("b", "b1"))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, second_a, first_b)
        assert order == ["a1", "b1", "a2"]

    asyncio.run(scenario())
