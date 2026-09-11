import asyncio
import time

import pytest

from taskhub_v2.domain.models import Plan, ProjectDefinition, ProjectSchedulingPolicy
from taskhub_v2.domain.production import ExecutionPlan, ProductionTask
from taskhub_v2.persistence.production import MemoryProductionStore
from taskhub_v2.services.dag_analysis import analyze_execution
from taskhub_v2.services.dag_plan import DagPlanService
from taskhub_v2.services.dag_readiness import waiting_reasons
from taskhub_v2.services.dag_scheduler import PersistentDagScheduler
from taskhub_v2.services.dag_scheduler_models import DagExecutionError
from taskhub_v2.services.fair_budget import FairProjectBudget
from tests.test_dag_orchestration import RecordingExecutor, contract, specification


def test_weighted_fair_budget_prioritizes_without_starving_other_projects():
    async def scenario():
        budget = FairProjectBudget(global_limit=1, default_project_limit=1)
        order = []
        release = asyncio.Event()

        async def work(project, name, weight=1, hold=False):
            async with budget.slot(project, priority_weight=weight):
                order.append(name)
                if hold:
                    await release.wait()

        first = asyncio.create_task(work("high", "h0", 3, True))
        await asyncio.sleep(0)
        queued = [asyncio.create_task(work("high", f"h{i}", 3)) for i in range(1, 7)]
        queued += [asyncio.create_task(work("low", f"l{i}")) for i in range(1, 3)]
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, *queued)
        assert order[1] == "l1"
        assert order.index("l2") < len(order) - 1
        assert sum(item.startswith("h") for item in order[:6]) > sum(
            item.startswith("l") for item in order[:6]
        )

    asyncio.run(scenario())


def test_global_budget_supports_twenty_concurrent_tasks_without_exceeding_limit():
    async def scenario():
        budget = FairProjectBudget(global_limit=20, default_project_limit=20)
        active = 0
        peak = 0
        all_started = asyncio.Event()
        release = asyncio.Event()

        async def work(index):
            nonlocal active, peak
            async with budget.slot(f"project-{index % 2}", project_limit=20):
                active += 1
                peak = max(peak, active)
                if active == 20:
                    all_started.set()
                await release.wait()
                active -= 1

        tasks = [asyncio.create_task(work(index)) for index in range(20)]
        await asyncio.wait_for(all_started.wait(), timeout=1)
        assert peak == 20
        release.set()
        await asyncio.gather(*tasks)

    asyncio.run(scenario())


def test_run_cost_budget_blocks_work_before_dispatch():
    async def scenario():
        store = MemoryProductionStore()
        plan = await store.save(
            ExecutionPlan(
                project_id="demo",
                plan_id="plan_budget",
                version=1,
                status="active",
                product_spec_id="ps_demo",
                product_spec_version=1,
                policy={"project_concurrency": 2, "run_cost_budget_units": 1},
                task_ids=["task_budget_medium"],
            )
        )
        await store.save(
            ProductionTask(
                project_id="demo",
                task_id="task_budget_medium",
                plan_id=plan.plan_id,
                plan_version=1,
                title="Medium task",
                objective="Do work",
                estimated_size="medium",
            )
        )
        executor = RecordingExecutor()
        with pytest.raises(DagExecutionError, match="Ready"):
            await PersistentDagScheduler(store, executor).execute(plan.plan_id)
        assert executor.calls == []
        task = await store.get("task", "task_budget_medium", "1")
        assert "成本预算" in task.waiting_reasons[0]

    asyncio.run(scenario())


def test_project_policy_is_frozen_into_new_execution_plan():
    async def scenario():
        store = MemoryProductionStore()
        project = ProjectDefinition(
            id="demo",
            repository="/tmp",
            scheduling_policy=ProjectSchedulingPolicy(
                concurrency_limit=7,
                priority_weight=4,
                run_cost_budget_units=250,
            ),
        )
        planner = DagPlanService(store, project_policy_resolver=lambda _project_id: project)
        bundle = await planner.compile(
            "demo",
            "run-policy",
            specification(),
            contract(),
            Plan(summary="policy", steps=["Implement API"], acceptance=["tests"]),
        )
        assert bundle.plan.policy == {
            "project_concurrency": 7,
            "priority_weight": 4,
            "run_cost_budget_units": 250,
            "dynamic_batches": True,
        }

    asyncio.run(scenario())


def test_thousand_task_analysis_and_ready_grouping_finish_below_target():
    tasks = []
    for index in range(1000):
        task_id = f"task_scale_{index:04d}"
        tasks.append(
            ProductionTask(
                project_id="scale",
                task_id=task_id,
                plan_id="plan_scale",
                plan_version=1,
                title=f"Task {index}",
                objective="Scale test",
                depends_on=[f"task_scale_{index - 1:04d}"] if index else [],
                required_capabilities=["coding", "python3"],
                resource_locks=[f"path:src/{index % 20}"],
            )
        )
    plan = ExecutionPlan(
        project_id="scale",
        plan_id="plan_scale",
        version=1,
        product_spec_id="ps_scale",
        product_spec_version=1,
        policy={"project_concurrency": 20, "run_cost_budget_units": 5000},
    )
    started = time.perf_counter()
    analysis = analyze_execution(plan, tasks, [], [])
    assert time.perf_counter() - started < 2
    assert analysis["task_counts"]["total"] == 1000
    assert len(analysis["prediction"]["critical_path_task_ids"]) == 1000
    assert analysis["bottlenecks"][0]["task_count"] == 50

    async def readiness():
        calls = 0

        async def capability(_task):
            nonlocal calls
            calls += 1
            return ""

        started_ready = time.perf_counter()
        reasons = await waiting_reasons(plan, tasks, set(), {}, capability)
        assert time.perf_counter() - started_ready < 2
        assert calls == 1
        assert reasons[tasks[0].task_id] == []

    asyncio.run(readiness())
