import argparse
import importlib.util
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parents[1] / "deploy" / "release" / "taskhub-project-client.py"


def load_module():
    spec = importlib.util.spec_from_file_location("taskhub_project_client", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeClient:
    def __init__(self, projects, runs=None, spec=None, run=None, contracts=None):
        self.projects = projects
        self.runs = runs or []
        self.spec = spec
        self.run = run
        self.contracts = contracts if contracts is not None else [active_contract()]
        self.posts = []

    def get(self, path, query=None):
        if path == "api/projects":
            return {"projects": self.projects}
        if path == "api/runs":
            return {"items": self.runs}
        if path == "api/product-specs/current":
            return {"product_spec": self.spec}
        if path.startswith("api/product-specs/"):
            return {"product_spec": self.spec}
        if path.endswith("/project-contracts"):
            return {"project_contracts": self.contracts}
        if path.startswith("api/runs/"):
            return self.run
        raise AssertionError((path, query))

    def post(self, path, body):
        self.posts.append((path, body))
        return {"run_id": "new-run", **body, "status": "running"}


def args(**values):
    defaults = {
        "command": "ensure-run",
        "project": "ecommerce-operations-platform",
        "production_line": "ops-data",
        "requirement": None,
        "requirement_file": None,
        "spec_id": None,
        "spec_version": None,
    }
    defaults.update(values)
    return argparse.Namespace(**defaults)


def project():
    return {
        "id": "ecommerce-operations-platform",
        "name": "电商运营平台",
        "repository": "/var/lib/taskhub/projects/ecommerce-operations-platform",
    }


def active_contract():
    return {
        "contract_id": "pc_ecommerce",
        "version": 2,
        "status": "active",
    }


def test_ensure_run_recovers_unique_non_terminal_run_without_creating():
    module = load_module()
    existing = {
        "run_id": "existing-run",
        "project_id": project()["id"],
        "production_line": "ops-data",
        "status": "blocked",
    }
    client = FakeClient([project()], [existing])

    result = module.execute(client, args())

    assert result["action"] == "recovered"
    assert result["run"] == existing
    assert client.posts == []


def test_ensure_run_creates_from_current_approved_spec_when_no_active_run():
    module = load_module()
    spec = {"spec_id": "ps_ops_data", "version": 3, "status": "approved"}
    client = FakeClient([project()], [], spec)

    result = module.execute(client, args(requirement="完成 OPS-DATA-001"))

    assert result["action"] == "created"
    assert client.posts == [
        (
            "api/runs",
            {
                "project_id": project()["id"],
                "production_line": "ops-data",
                "requirement": "完成 OPS-DATA-001",
                "product_spec_id": "ps_ops_data",
                "product_spec_version": 3,
            },
        )
    ]


def test_ensure_run_fails_closed_for_multiple_active_runs():
    module = load_module()
    runs = [
        {"run_id": "one", "status": "running"},
        {"run_id": "two", "status": "waiting"},
    ]

    with pytest.raises(module.TaskHubClientError, match="multiple active runs"):
        module.execute(FakeClient([project()], runs), args())


def test_create_run_requires_approved_product_spec():
    module = load_module()
    client = FakeClient(
        [project()], [], {"spec_id": "ps_ops_data", "version": 3, "status": "draft"}
    )

    with pytest.raises(module.TaskHubClientError, match="product_spec_not_approved"):
        module.execute(client, args(command="create-run"))


def test_readiness_reports_all_governance_blockers_with_next_actions():
    module = load_module()
    spec = {"spec_id": "ps_ops_data", "version": 3, "status": "in_review"}

    result = module.execute(
        FakeClient([project()], spec=spec, contracts=[]),
        args(command="readiness"),
    )

    assert result["ready"] is False
    assert result["product_spec"] == {
        "spec_id": "ps_ops_data",
        "version": 3,
        "status": "in_review",
    }
    assert result["project_contract"] is None
    assert [item["code"] for item in result["blockers"]] == [
        "product_spec_not_approved",
        "project_contract_not_active",
    ]


def test_readiness_accepts_an_explicit_approved_specification_version():
    module = load_module()
    spec = {"spec_id": "ps_ops_data", "version": 2, "status": "approved"}

    result = module.execute(
        FakeClient([project()], spec=spec),
        args(
            command="readiness",
            spec_id="ps_ops_data",
            spec_version=2,
        ),
    )

    assert result["ready"] is True
    assert result["blockers"] == []


def test_create_run_fails_before_post_when_contract_is_not_active():
    module = load_module()
    spec = {"spec_id": "ps_ops_data", "version": 3, "status": "approved"}
    client = FakeClient([project()], spec=spec, contracts=[])

    with pytest.raises(module.TaskHubClientError, match="project_contract_not_active"):
        module.execute(client, args(command="create-run"))

    assert client.posts == []


def test_project_resolution_rejects_ambiguous_names():
    module = load_module()
    first = {**project(), "id": "first", "name": "shared"}
    duplicate = {**project(), "id": "another", "name": "shared"}

    with pytest.raises(module.TaskHubClientError, match="multiple registered projects"):
        module.resolve_project(FakeClient([first, duplicate]), "shared")


def test_status_defaults_to_bounded_agent_summary():
    module = load_module()
    run = {
        "run_id": "run-1",
        "project_id": "demo",
        "production_line": "default",
        "stage": "implementation",
        "status": "running",
        "revision_count": 1,
        "max_revision_attempts": 2,
        "production_tasks": [{"status": "running"}, {"status": "pending"}],
        "requirement": "large payload omitted",
    }

    result = module.execute(
        FakeClient([], run=run),
        argparse.Namespace(command="status", run_id="run-1", full=False),
    )

    assert result["task_counts"] == {"running": 1, "pending": 1}
    assert "requirement" not in result


def test_archive_uses_explicit_run_id():
    module = load_module()
    client = FakeClient([])

    result = module.execute(
        client,
        argparse.Namespace(command="archive", run_id="run-terminal"),
    )

    assert result["run_id"] == "new-run"
    assert client.posts == [("api/runs/run-terminal/archive", {})]
