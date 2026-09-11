import asyncio
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from taskhub_v2.domain.dag import DagExecutionSnapshot, ExecutionBatch
from taskhub_v2.domain.models import RunStatus, RunView, Stage
from taskhub_v2.domain.production import (
    CapabilityPack,
    ChangeRequest,
    ExecutionPlan,
    ProductionTask,
    ProductSpec,
    ProductSpecStatus,
    TaskAttempt,
)
from taskhub_v2.domain.project_contract import ModuleContract, ProjectContract
from taskhub_v2.persistence.production import (
    MemoryProductionStore,
    PostgresProductionStore,
    ProductionObjectConflictError,
)
from taskhub_v2.services.legacy_plan import legacy_linear_plan


def product_spec(**updates):
    values = {
        "project_id": "project-a",
        "spec_id": "ps_project_a",
        "version": 1,
        "goals": ["Ship a traceable release"],
    }
    values.update(updates)
    return ProductSpec(**values)


def test_product_spec_requires_an_explicit_version_chain():
    with pytest.raises(ValidationError, match="immediate predecessor"):
        product_spec(version=3, previous_version=1)


def test_task_rejects_self_dependency():
    with pytest.raises(ValidationError, match="depend on itself"):
        ProductionTask(
            project_id="project-a",
            task_id="task_build_app",
            plan_id="plan_project_a",
            plan_version=1,
            title="Build",
            objective="Build app",
            depends_on=["task_build_app"],
        )


def test_memory_store_persists_all_phase_zero_objects():
    async def scenario():
        store = MemoryProductionStore()
        records = [
            product_spec(),
            ExecutionPlan(
                project_id="project-a",
                plan_id="plan_project_a",
                version=1,
                product_spec_id="ps_project_a",
                product_spec_version=1,
            ),
            ProductionTask(
                project_id="project-a",
                task_id="task_build_app",
                plan_id="plan_project_a",
                plan_version=1,
                title="Build",
                objective="Build app",
            ),
            TaskAttempt(
                project_id="project-a",
                attempt_id="attempt_build_1",
                task_id="task_build_app",
                attempt_number=1,
            ),
            ChangeRequest(
                project_id="project-a",
                change_request_id="cr_scope_001",
                reason="Requirement changed",
                source_event="requirement.updated",
            ),
            CapabilityPack(
                project_id="project-a",
                pack_id="pack_frontend_style",
                version="1.0.0",
                pack_type="frontend-style",
                source="builtin",
                summary="Default style",
            ),
            ProjectContract(
                project_id="project-a",
                contract_id="pc_project_a",
                version=1,
                profile_id="python-service",
                modules=[ModuleContract(name="domain", paths=["src/domain/**"])],
            ),
            ExecutionBatch(
                project_id="project-a",
                batch_id="batch_project_a_001",
                plan_id="plan_project_a",
                plan_version=1,
                sequence=1,
                task_ids=["task_build_app"],
            ),
            DagExecutionSnapshot(
                project_id="project-a",
                snapshot_id="snapshot_project_a",
                plan_id="plan_project_a",
                plan_version=1,
                sequence=1,
                status="planning",
            ),
        ]
        for record in records:
            stored = await store.save(record)
            object_type, object_id, revision = {
                ProductSpec: ("product_spec", "ps_project_a", "1"),
                ExecutionPlan: ("execution_plan", "plan_project_a", "1"),
                ProductionTask: ("task", "task_build_app", "1"),
                TaskAttempt: ("task_attempt", "attempt_build_1", "1"),
                ChangeRequest: ("change_request", "cr_scope_001", "1"),
                CapabilityPack: ("capability_pack", "pack_frontend_style", "1.0.0"),
                ProjectContract: ("project_contract", "pc_project_a", "1"),
                ExecutionBatch: ("execution_batch", "batch_project_a_001", "1"),
                DagExecutionSnapshot: ("dag_snapshot", "snapshot_project_a", "1"),
            }[type(record)]
            assert await store.get(object_type, object_id, revision) == stored
            assert len(stored.content_digest) == 64
        assert len(await store.list(project_id="project-a")) == 9

    asyncio.run(scenario())


def test_store_allows_state_transition_but_rejects_content_rewrite():
    async def scenario():
        store = MemoryProductionStore()
        original = await store.save(product_spec())
        revised = original.model_copy(update={"goals": ["Reviewed goal"]})
        revised = await store.save(revised)
        reviewed = revised.model_copy(update={"status": ProductSpecStatus.IN_REVIEW})
        assert (await store.save(reviewed)).status == ProductSpecStatus.IN_REVIEW
        with pytest.raises(ProductionObjectConflictError, match="illegal"):
            await store.save(reviewed.model_copy(update={"status": ProductSpecStatus.SUPERSEDED}))
        approved = await store.save(
            reviewed.model_copy(update={"status": ProductSpecStatus.APPROVED})
        )
        with pytest.raises(ProductionObjectConflictError, match="immutable"):
            await store.save(approved.model_copy(update={"goals": ["Silently changed"]}))

    asyncio.run(scenario())


def test_legacy_run_maps_to_one_plan_task_and_dynamic_batch():
    now = datetime.now(UTC)
    run = RunView(
        run_id="legacy-run-1",
        project_id="project-a",
        requirement="Add export support",
        production_line="default",
        created_at=now,
        updated_at=now,
        stage=Stage.IMPLEMENTATION,
        status=RunStatus.RUNNING,
        next_nodes=["implementation"],
    )
    view = legacy_linear_plan(run)
    assert view.execution_plan.legacy_run_id == run.run_id
    assert view.execution_plan.policy == {"production_line": "default"}
    assert view.execution_plan.task_ids == [view.task.task_id]
    assert view.batch.task_ids == [view.task.task_id]
    assert view.batch.compatibility_mode == "legacy-linear"


def test_postgres_store_round_trip(postgres_dsn):
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    async def scenario():
        async with AsyncConnectionPool(
            postgres_dsn, kwargs={"autocommit": True, "row_factory": dict_row}
        ) as pool:
            store = PostgresProductionStore(pool)
            await store.setup()
            stored = await store.save(product_spec())
            assert await store.get("product_spec", "ps_project_a", "1") == stored
            assert await store.list(project_id="project-a", object_type="product_spec") == [stored]
            contract = await store.save(
                ProjectContract(
                    project_id="project-a",
                    contract_id="pc_project_a",
                    version=1,
                    profile_id="python-service",
                    modules=[ModuleContract(name="domain", paths=["src/domain/**"])],
                )
            )
            assert await store.get("project_contract", "pc_project_a", "1") == contract

    asyncio.run(scenario())
