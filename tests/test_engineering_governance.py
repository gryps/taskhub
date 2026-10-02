import asyncio
import subprocess
from datetime import UTC, datetime, timedelta

from taskhub_v2.domain.governance import (
    EngineeringPolicy,
    EngineeringPolicyStatus,
    PolicyExceptionStatus,
)
from taskhub_v2.domain.models import ProjectDefinition
from taskhub_v2.persistence.production import MemoryProductionStore
from taskhub_v2.projects import ProjectRegistry
from taskhub_v2.services.engineering_governance import EngineeringGovernanceService
from taskhub_v2.services.project_contracts import ProjectContractService


def _repository(path):
    path.mkdir()
    subprocess.run(["git", "-C", str(path), "init", "-b", "main"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@taskhub.local"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "TaskHub Test"],
        check=True,
    )
    (path / "README.md").write_text("# Demo\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "initial"], check=True)
    return path


async def _services(tmp_path):
    repository = _repository(tmp_path / "repository")
    projects = ProjectRegistry(str(tmp_path / "projects.json"))
    projects.add(ProjectDefinition(id="demo", name="Demo", repository=str(repository)))
    store = MemoryProductionStore()
    governance = EngineeringGovernanceService(store)
    await governance.ensure_builtin()
    contracts = ProjectContractService(store, projects, governance=governance)
    return repository, store, governance, contracts


def test_new_contract_freezes_active_global_policy(tmp_path):
    async def scenario():
        _repository_path, _store, governance, contracts = await _services(tmp_path)
        draft = await contracts.create_draft(
            "demo", profile_id="frontend-spa", inferred=False, actor="owner"
        )
        return governance, draft

    governance, draft = asyncio.run(scenario())
    active = asyncio.run(governance.active_policy())
    assert draft.engineering_policy.policy_id == active.policy_id
    assert draft.engineering_policy.policy_version == active.version
    assert draft.engineering_policy.policy_digest == active.content_digest
    assert "frontend.foundation" in draft.engineering_policy.rule_ids
    assert "docs/FRONTEND_ARCHITECTURE.md" in draft.documentation_files
    assert all(module.max_file_lines <= 400 for module in draft.modules)


def test_policy_revision_does_not_mutate_existing_contract_binding(tmp_path):
    async def scenario():
        _repository_path, _store, governance, contracts = await _services(tmp_path)
        draft = await contracts.create_draft(
            "demo", profile_id="backend-api", inferred=False, actor="owner"
        )
        original = draft.engineering_policy.model_copy(deep=True)
        active = await governance.active_policy()
        revised = await governance.create_draft(
            name="Revised policy",
            rules=active.rules,
            source_reference="global-rules-v2",
            actor="admin",
        )
        await governance.activate(revised.version, "admin")
        return draft, original, await governance.binding_status(draft)

    draft, original, status = asyncio.run(scenario())
    assert status["valid"] is True
    assert status["current"] is False
    assert draft.engineering_policy == original


def test_project_view_exposes_the_frozen_contract_rule_subset(tmp_path):
    async def scenario():
        _repository_path, _store, governance, contracts = await _services(tmp_path)
        contract = await contracts.create_draft(
            "demo", profile_id="frontend-spa", inferred=False, actor="owner"
        )
        return contract, await governance.project_view("demo", contract)

    contract, view = asyncio.run(scenario())
    binding = view["contract_binding"]
    assert view["binding"]["valid"] is True
    assert binding == contract.engineering_policy
    assert binding.rule_ids
    assert "frontend.foundation" in binding.rule_ids
    assert len(binding.instructions) == len(binding.rule_ids)
    expected = [
        rule.rule_id
        for rule in view["binding"]["policy"].rules
        if rule.applies(contract.profile_id)
    ]
    assert binding.rule_ids == expected


def test_approved_project_exception_waives_only_named_rule(tmp_path):
    async def scenario():
        repository, _store, governance, contracts = await _services(tmp_path)
        contract = await contracts.create_draft(
            "demo", profile_id="backend-api", inferred=False, actor="owner"
        )
        evidence = {"delivery_touch-governance": "diff and dependency review passed"}
        before = await governance.gate_findings(repository, contract, evidence)
        exception = await governance.propose_exception(
            "demo",
            rule_ids=["foundation.project-baseline"],
            scope="project",
            scope_ids=[],
            reason="Legacy repository migration cannot create every document in this batch.",
            risk="Architecture knowledge remains incomplete during the bounded migration window.",
            controls=["Block unrelated feature work until the baseline documents are created."],
            recovery_condition="Create and approve every required engineering baseline document.",
            expires_at=datetime.now(UTC) + timedelta(days=14),
            actor="owner",
        )
        approved = await governance.decide_exception(
            "demo", exception.exception_id, approve=True, actor="admin"
        )
        after = await governance.gate_findings(repository, contract, evidence)
        return before, approved, after

    before, approved, after = asyncio.run(scenario())
    assert any(
        item.status == "failed" and "foundation.project-baseline" in item.gate_id
        for item in before
    )

    assert approved.status == PolicyExceptionStatus.APPROVED

    waived = [item for item in after if "foundation.project-baseline" in item.gate_id]
    assert waived and all(item.status == "warning" for item in waived)


def test_policy_objects_round_trip_through_production_store(tmp_path):
    async def scenario():
        _repository_path, store, governance, _contracts = await _services(tmp_path)
        active = await governance.active_policy()
        restored = await store.get("engineering_policy", active.policy_id, str(active.version))
        return restored

    restored = asyncio.run(scenario())
    assert isinstance(restored, EngineeringPolicy)
    assert restored.status == EngineeringPolicyStatus.ACTIVE
