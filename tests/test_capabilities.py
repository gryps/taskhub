import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from taskhub_v2.api.app import _required_permission, create_app
from taskhub_v2.config import Settings
from taskhub_v2.domain.capability import CapabilityPackLock
from taskhub_v2.domain.models import ExecutionResult, Plan, ProjectDefinition
from taskhub_v2.domain.production import ProductSpec, ProductSpecStatus, TaskAttempt
from taskhub_v2.domain.project_contract import ContractCommands, ModuleContract, ProjectContract
from taskhub_v2.persistence.production import MemoryProductionStore
from taskhub_v2.services.capabilities import CapabilityConflictError, CapabilityService
from taskhub_v2.services.dag_plan import DagPlanService, DagPlanValidationError
from taskhub_v2.workers.dag_executor import WorkerDagExecutor


class Projects:
    def get(self, project_id):
        if project_id != "demo":
            raise LookupError(project_id)
        return ProjectDefinition(id="demo", repository="/tmp")


def contract():
    return ProjectContract(
        project_id="demo",
        contract_id="pc_demo",
        version=1,
        status="active",
        profile_id="frontend-spa",
        languages=["typescript"],
        frameworks=["react"],
        modules=[ModuleContract(name="web", paths=["src/**"])],
        commands=ContractCommands(test=[["npm", "test"]]),
    )


def specification():
    return ProductSpec(
        project_id="demo",
        spec_id="ps_demo",
        version=1,
        goals=["交付响应式前端"],
        acceptance_criteria=["桌面和移动端通过"],
    )


async def prepared_service():
    store = MemoryProductionStore()
    await store.save(contract())
    await store.save(specification())
    service = CapabilityService(store, Projects())
    await service.ensure_builtins()
    return store, service


def test_recommends_three_compatible_frontend_profiles_with_previews():
    async def scenario():
        _store, service = await prepared_service()
        recommendations = await service.recommendations("demo", "ps_demo", 1)
        assert len(recommendations) == 3
        assert all(item.compatibility["compatible"] for item in recommendations)
        assert all(len(item.pack_refs) == 4 for item in recommendations)
        assert all(
            [preview["surface"] for preview in item.previews]
            == ["login", "list", "detail", "mobile"]
            for item in recommendations
        )

    asyncio.run(scenario())


def test_lock_compiles_design_contract_and_upgrade_requires_explicit_activation():
    async def scenario():
        store, service = await prepared_service()
        recommendations = await service.recommendations("demo", "ps_demo", 1)
        first = await service.create_lock(
            "demo", "ps_demo", 1, recommendations[0].pack_refs, "owner"
        )
        active, design = await service.activate_lock("demo", first.version, "owner")
        assert active.status == "active" and design.lock_version == active.version
        assert design.design_tokens and design.accessibility_rules
        locked_spec = await store.get("product_spec", "ps_demo", "1")
        assert locked_spec.capability_pack_lock == recommendations[0].pack_refs

        upgrade = await service.create_lock(
            "demo", "ps_demo", 1, recommendations[1].pack_refs, "owner"
        )
        assert upgrade.status == "draft" and upgrade.migration_tasks
        before = await service.current("demo")
        assert before["active_lock"].version == first.version
        upgraded, new_design = await service.activate_lock("demo", upgrade.version, "owner")
        assert upgraded.status == "active" and new_design.migration_tasks
        old = await store.get("capability_pack_lock", "lock_demo", str(first.version))
        assert old.status == "superseded"

    asyncio.run(scenario())


def test_untrusted_incompatible_and_unsafe_imports_are_blocked():
    async def scenario():
        _store, service = await prepared_service()
        with pytest.raises(ValidationError, match="cannot traverse"):
            await service.import_manifest(
                {
                    "pack_id": "pack_unsafe",
                    "version": "1.0.0",
                    "pack_type": "frontend-style",
                    "summary": "unsafe paths",
                    "license": "Apache-2.0",
                    "files": ["../secret"],
                },
                "admin",
            )
        with pytest.raises(CapabilityConflictError, match="credentials"):
            await service.import_manifest(
                {
                    "pack_id": "pack_secret",
                    "version": "1.0.0",
                    "pack_type": "frontend-style",
                    "summary": "contains secret",
                    "license": "Apache-2.0",
                    "content": {"token": "ghp_" + "x" * 40},
                },
                "admin",
            )
        draft = await service.import_manifest(
            {
                "pack_id": "pack_incompatible",
                "version": "1.0.0",
                "pack_type": "frontend-style",
                "summary": "backend only",
                "license": "Apache-2.0",
                "compatibility": {"project_profiles": ["backend-api"]},
            },
            "admin",
        )
        recommendations = await service.recommendations("demo", "ps_demo", 1)
        selected = [draft.ref, *recommendations[0].pack_refs[1:]]
        with pytest.raises(CapabilityConflictError, match="not trusted"):
            await service.create_lock("demo", "ps_demo", 1, selected, "owner")
        trusted = await service.trust(draft.pack_id, draft.version, "admin")
        selected[0] = trusted.ref
        with pytest.raises(CapabilityConflictError, match="does not support profile"):
            await service.create_lock("demo", "ps_demo", 1, selected, "owner")

    asyncio.run(scenario())


def test_capability_api_recommends_locks_and_gates_frontend_spec(tmp_path):
    projects = tmp_path / "projects.json"
    projects.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "id": "demo",
                        "name": "Demo",
                        "repository": str(tmp_path),
                        "base_ref": "main",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(
        checkpointer="memory",
        provider="deterministic",
        admin_token="capability-admin",
        session_secret="capability-session",
        projects_file=str(projects),
        production_orchestration_enabled=True,
        operations_log_file=str(tmp_path / "operations.jsonl"),
    )
    with TestClient(create_app(settings)) as client:
        login = client.post("/api/auth/login", json={"token": "capability-admin"})
        headers = {"X-CSRF-Token": login.cookies["taskhub_v2_csrf"]}
        packs = client.get("/api/capability-packs").json()["capability_packs"]
        assert len(packs) >= 7 and all(item["manifest_digest"] for item in packs)
        draft = client.post(
            "/api/projects/demo/project-contracts/draft",
            headers=headers,
            json={"profile_id": "frontend-spa", "inferred": False},
        ).json()
        contract_root = (
            f"/api/projects/demo/project-contracts/{draft['contract_id']}"
            f"/versions/{draft['version']}"
        )
        client.post(f"{contract_root}/review", headers=headers)
        client.post(f"{contract_root}/activate", headers=headers)
        created = client.post(
            "/api/requirements",
            headers=headers,
            json={
                "project_id": "demo",
                "original_text": (
                    "为管理员用户增加响应式状态页。\n"
                    "验收标准：桌面和手机页面显示服务状态。\n"
                    "部署到现有预生产环境。"
                ),
            },
        ).json()
        spec = created["product_spec"]
        query = f"spec_id={spec['spec_id']}&spec_version={spec['version']}"
        recommendations = client.get(
            f"/api/projects/demo/capability-recommendations?{query}"
        ).json()["recommendations"]
        assert len(recommendations) == 3
        lock = client.post(
            "/api/projects/demo/capability-locks",
            headers=headers,
            json={
                "spec_id": spec["spec_id"],
                "spec_version": spec["version"],
                "pack_refs": recommendations[0]["pack_refs"],
            },
        ).json()
        activated = client.post(
            f"/api/projects/demo/capability-locks/{lock['version']}/activate",
            headers=headers,
        )
        assert activated.status_code == 200
        current = client.get("/api/projects/demo/capability-lock").json()
        assert current["active_lock"]["pack_refs"] == recommendations[0]["pack_refs"]
        assert current["design_contract"]["validation_evidence"]
        spec_root = f"/api/product-specs/{spec['spec_id']}/versions/{spec['version']}"
        assert (
            client.post(f"{spec_root}/review?project_id=demo", headers=headers).status_code == 200
        )
        assert (
            client.post(f"{spec_root}/approve?project_id=demo", headers=headers).status_code == 200
        )

    assert _required_permission("/api/capability-packs/import", "POST") == ("infrastructure:manage")
    assert _required_permission("/api/projects/demo/capability-locks", "POST") == (
        "projects:manage"
    )


def test_locked_design_contract_is_frozen_into_plan_and_worker_context():
    class Worker:
        def __init__(self):
            self.context = None

        async def execute(self, *args, **kwargs):
            self.context = kwargs["task_context"]
            return ExecutionResult(summary="done")

    async def scenario():
        store, service = await prepared_service()
        recommendation = (await service.recommendations("demo", "ps_demo", 1))[0]
        draft = await service.create_lock("demo", "ps_demo", 1, recommendation.pack_refs, "owner")
        lock, design = await service.activate_lock("demo", draft.version, "owner")
        spec = await store.get("product_spec", "ps_demo", "1")
        spec = await store.save(spec.model_copy(update={"status": ProductSpecStatus.IN_REVIEW}))
        spec = await store.save(spec.model_copy(update={"status": ProductSpecStatus.APPROVED}))
        planner = DagPlanService(store, design_contract_resolver=service.design_for_refs)
        bundle = await planner.compile(
            "demo",
            "run-design",
            spec,
            contract(),
            Plan(summary="frontend", steps=["Implement page"], acceptance=["review"]),
        )
        assert bundle.plan.capability_lock_id == lock.lock_id
        assert bundle.plan.design_contract_id == design.contract_id
        assert f"{design.contract_id}:v{design.version}" in bundle.tasks[0].contracts
        assert "visual regression comparison" in bundle.tasks[0].required_evidence
        worker = Worker()
        executor = WorkerDagExecutor(worker, store, Projects())
        await executor.execute(
            bundle.tasks[0],
            TaskAttempt(
                project_id="demo",
                attempt_id="attempt_design_1",
                task_id=bundle.tasks[0].task_id,
                attempt_number=1,
            ),
            base_commit="",
        )
        assert worker.context["project_design_contract"]["pack_refs"] == lock.pack_refs
        assert (
            "visual regression comparison"
            in worker.context["project_design_contract"]["validation_evidence"]
        )

    asyncio.run(scenario())


def test_frontend_plan_cannot_start_without_exact_capability_lock():
    async def scenario():
        store = MemoryProductionStore()
        with pytest.raises(DagPlanValidationError, match="必须先锁定"):
            await DagPlanService(store).compile(
                "demo",
                "run-unlocked",
                specification(),
                contract(),
                Plan(summary="frontend", steps=["Implement page"], acceptance=["review"]),
            )

    asyncio.run(scenario())


def test_frontend_spec_cannot_be_approved_without_active_lock():
    async def scenario():
        store, service = await prepared_service()
        spec = await store.get("product_spec", "ps_demo", "1")
        with pytest.raises(CapabilityConflictError, match="requires an active"):
            await service.validate_spec_lock(spec)

    asyncio.run(scenario())


def test_lock_revalidates_trust_and_rejects_stale_or_floating_versions():
    async def scenario():
        _store, service = await prepared_service()
        recommendations = await service.recommendations("demo", "ps_demo", 1)
        first = await service.create_lock(
            "demo", "ps_demo", 1, recommendations[0].pack_refs, "owner"
        )
        second = await service.create_lock(
            "demo", "ps_demo", 1, recommendations[1].pack_refs, "owner"
        )
        with pytest.raises(CapabilityConflictError, match="stale"):
            await service.activate_lock("demo", first.version, "owner")
        pack_id, version = second.pack_refs[0].rsplit("@", 1)
        await service.set_enabled(pack_id, version, False, "security review")
        with pytest.raises(CapabilityConflictError, match="no longer trusted"):
            await service.activate_lock("demo", second.version, "owner")
        await service.set_enabled(pack_id, version, True)
        active, _design = await service.activate_lock("demo", second.version, "owner")
        assert active.status == "active"

    asyncio.run(scenario())

    with pytest.raises(ValidationError, match="exact pack versions"):
        CapabilityPackLock(
            project_id="demo",
            lock_id="lock_demo",
            version=1,
            spec_id="ps_demo",
            spec_version=1,
            pack_refs=["pack_style@latest"],
        )


def test_revised_spec_requires_a_new_lock_even_when_pack_versions_are_unchanged():
    async def scenario():
        store, service = await prepared_service()
        refs = (await service.recommendations("demo", "ps_demo", 1))[0].pack_refs
        first = await service.create_lock("demo", "ps_demo", 1, refs, "owner")
        await service.activate_lock("demo", first.version, "owner")
        original = await store.get("product_spec", "ps_demo", "1")
        revised = await store.save(
            original.model_copy(
                update={
                    "version": 2,
                    "previous_version": 1,
                    "status": ProductSpecStatus.DRAFT,
                    "content_digest": "",
                }
            )
        )
        with pytest.raises(CapabilityConflictError, match="do not match"):
            await service.validate_spec_lock(revised)
        second = await service.create_lock("demo", "ps_demo", 2, refs, "owner")
        assert second.migration_tasks == []
        _active, design = await service.activate_lock("demo", second.version, "owner")
        assert design.spec_version == 2
        await service.validate_spec_lock(await store.get("product_spec", "ps_demo", "2"))

    asyncio.run(scenario())
