import json
import subprocess
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from taskhub_v2.api.app import create_app
from taskhub_v2.artifacts import ArtifactStore
from taskhub_v2.config import Settings
from taskhub_v2.domain.models import (
    ExecutionResult,
    ProjectDefinition,
    ScheduledTests,
    TestExecution,
    Workspace,
)
from taskhub_v2.domain.project_contract import (
    ArtifactContract,
    ContractCommands,
    InterfaceContract,
    MigrationContract,
    ModuleContract,
    ProjectContract,
)
from taskhub_v2.persistence.production import MemoryProductionStore
from taskhub_v2.projects import ProjectRegistry
from taskhub_v2.services.contract_documents import render_contract_documents
from taskhub_v2.services.contract_gates import validate_repository
from taskhub_v2.services.project_contracts import ProjectContractService
from taskhub_v2.workers.acceptance import ProjectAcceptanceGateway


def git(repository: Path, *arguments: str):
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def repository(path: Path, files: dict[str, str | bytes] | None = None) -> Path:
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.email", "test@taskhub.local")
    git(path, "config", "user.name", "TaskHub Test")
    for relative, content in (files or {"README.md": "# Test\n"}).items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
    git(path, "add", "-f", ".")
    git(path, "commit", "-m", "initial")
    return path


def settings(tmp_path, repo):
    projects = tmp_path / "projects.json"
    projects.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "id": "demo",
                        "name": "Demo",
                        "repository": str(repo),
                        "base_ref": "main",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return Settings(
        checkpointer="memory",
        provider="deterministic",
        admin_token="admin-secret",
        session_secret="session-secret",
        projects_file=str(projects),
        production_orchestration_enabled=True,
        operations_log_file=str(tmp_path / "operations.jsonl"),
    )


def login(client):
    response = client.post("/api/auth/login", json={"token": "admin-secret"})
    return {"X-CSRF-Token": response.cookies["taskhub_v2_csrf"]}


def test_official_profiles_and_contract_lifecycle(tmp_path):
    repo = repository(tmp_path / "repo")
    with TestClient(create_app(settings(tmp_path, repo))) as client:
        headers = login(client)
        profiles = client.get("/api/project-contract-profiles").json()["profiles"]
        assert {item["profile_id"] for item in profiles} == {
            "fullstack-web",
            "backend-api",
            "frontend-spa",
            "python-service",
            "worker-service",
        }
        draft = client.post(
            "/api/projects/demo/project-contracts/draft",
            headers=headers,
            json={"profile_id": "backend-api", "inferred": False},
        ).json()
        assert draft["status"] == "draft"
        assert draft["profile_id"] == "backend-api"
        root = (
            f"/api/projects/demo/project-contracts/{draft['contract_id']}"
            f"/versions/{draft['version']}"
        )
        documents = client.get(f"{root}/documents").json()["documents"]
        assert set(documents) == {
            ".taskhub/project.yaml",
            ".taskhub/architecture.yaml",
            ".taskhub/acceptance.yaml",
        }
        assert yaml.safe_load(documents[".taskhub/project.yaml"])["profile"] == "backend-api"
        assert client.post(f"{root}/review", headers=headers).json()["status"] == "in_review"
        assert client.post(f"{root}/activate", headers=headers).json()["status"] == "active"
        immutable = client.put(
            root,
            headers=headers,
            json={"languages": ["silently changed"]},
        )
        assert immutable.status_code == 409

        revision = client.post(
            "/api/projects/demo/project-contracts/draft",
            headers=headers,
            json={"profile_id": "backend-api", "inferred": False, "force_revision": True},
        ).json()
        assert revision["version"] == 2 and revision["previous_version"] == 1
        revision_root = (
            f"/api/projects/demo/project-contracts/{revision['contract_id']}"
            f"/versions/{revision['version']}"
        )
        client.post(f"{revision_root}/review", headers=headers)
        client.post(f"{revision_root}/activate", headers=headers)
        versions = client.get("/api/projects/demo/project-contracts").json()["project_contracts"]
        assert [item["status"] for item in versions] == ["active", "superseded"]


def contract(**updates) -> ProjectContract:
    values = {
        "project_id": "demo",
        "contract_id": "pc_demo",
        "version": 1,
        "profile_id": "backend-api",
        "directory_structure": ["src"],
        "modules": [
            ModuleContract(name="api", paths=["src/api/**"], may_import=["domain"]),
            ModuleContract(name="domain", paths=["src/domain/**"]),
        ],
        "artifacts": ArtifactContract(required_files=[]),
        "documentation_files": [],
    }
    values.update(updates)
    return ProjectContract(**values)


def test_gates_reject_architecture_api_migration_security_and_binary_violations(tmp_path):
    source = "openapi: 3.1.0\n"
    repo = repository(
        tmp_path / "bad",
        {
            "Dockerfile": "HEALTHCHECK CMD curl -f http://localhost/health\n",
            "compose.yaml": "healthcheck: {test: [CMD, curl, /health]}\n",
            ".env.example": "APP_MODE=example\n",
            "src/api/handler.py": "from domain.model import value\n" + "# line\n" * 24,
            "src/domain/model.py": "from api.handler import value\nQUERY = 'SELECT * FROM users'\n",
            "migrations/0001_first.sql": "SELECT 1;\n",
            "migrations/0001_second.sql": "SELECT 2;\n",
            "openapi.yaml": source,
            "src/generated/client.ts": "export const client = true;\n",
            ".taskhub/openapi.sha256": "wrong\n",
            "secret.txt": "-----BEGIN OPENSSH " "PRIVATE KEY-----\nplaceholder\n",
            "assets/logo.png": b"\x89PNG\x00placeholder",
        },
    )
    checked = contract(
        modules=[
            ModuleContract(
                name="api",
                paths=["src/api/**"],
                may_import=["domain"],
                max_file_lines=20,
            ),
            ModuleContract(name="domain", paths=["src/domain/**"]),
        ],
        interfaces=[
            InterfaceContract(
                name="public_api",
                source="openapi.yaml",
                generated_paths=["src/generated/**"],
                digest_file=".taskhub/openapi.sha256",
            )
        ],
        migrations=MigrationContract(paths=["migrations"], rollback_required=True),
    )
    report = validate_repository(repo, checked)
    summaries = "\n".join(item.summary for item in report.findings)
    assert report.status == "failed"
    assert "不允许依赖" in summaries
    assert "存在循环" in summaries
    assert "复杂度" in summaries or "超过 20 行" in summaries
    assert "不允许直接访问数据库" in summaries
    assert "摘要不一致" in summaries
    assert "迁移序号重复" in summaries and "缺少回滚" in summaries
    assert "疑似凭据" in summaries
    assert "缺少许可证清单" in summaries


def test_rendered_contract_documents_and_compliant_repository_pass(tmp_path):
    checked = contract()
    documents = render_contract_documents(checked)
    files = {
        "Dockerfile": "HEALTHCHECK CMD curl -f http://localhost/health\n",
        "compose.yaml": "healthcheck: {test: [CMD, curl, /health]}\n",
        ".env.example": "APP_MODE=example\n",
        "src/api/handler.py": "from domain.model import value\n",
        "src/domain/model.py": "value = 1\n",
        **documents,
    }
    repo = repository(tmp_path / "good", files)
    report = validate_repository(repo, checked)
    assert report.status == "passed"
    assert not [item for item in report.findings if item.status == "failed"]


class FailedScheduler:
    async def run(self, *args, **kwargs):
        return ScheduledTests(
            node_id="test-node",
            tests=[TestExecution(command=["project-gate"], exit_code=1, output_tail="failed")],
        )


def test_declared_gate_commands_are_executed_and_fail_the_report(tmp_path):
    repo = repository(
        tmp_path / "commands",
        {
            "Dockerfile": "HEALTHCHECK /health\n",
            "compose.yaml": "healthcheck: /health\n",
            ".env.example": "MODE=test\n",
            "src/domain/model.py": "value = 1\n",
        },
    )
    registry_path = tmp_path / "projects.json"
    registry = ProjectRegistry(str(registry_path))
    registry.add(ProjectDefinition(id="demo", repository=str(repo)))
    store = MemoryProductionStore()
    service = ProjectContractService(store, registry, FailedScheduler())
    active = contract(
        status="active",
        modules=[ModuleContract(name="domain", paths=["src/domain/**"])],
        commands=ContractCommands(architecture=[["project-gate"]]),
    )

    async def scenario():
        await store.save(active)
        return await service.gate("demo", strict=True, execute_commands=True)

    import asyncio

    report = asyncio.run(scenario())
    assert report.status == "failed"
    assert any(item.gate_id == "command:0" for item in report.findings)


class PassingScheduler:
    async def run(self, job_id, sticky_key, commands, *args, **kwargs):
        return ScheduledTests(
            node_id="test-node",
            tests=[
                TestExecution(command=command, exit_code=0, output_tail="ok")
                for command in commands
            ],
        )


def test_declared_artifact_must_exist_after_build_command(tmp_path):
    repo = repository(
        tmp_path / "missing-artifact",
        {
            "Dockerfile": "HEALTHCHECK /health\n",
            "compose.yaml": "healthcheck: /health\n",
            ".env.example": "MODE=test\n",
            "src/domain/model.py": "value = 1\n",
        },
    )
    registry = ProjectRegistry(str(tmp_path / "artifact-projects.json"))
    registry.add(ProjectDefinition(id="demo", repository=str(repo)))
    store = MemoryProductionStore()
    service = ProjectContractService(store, registry, PassingScheduler())
    active = contract(
        status="active",
        modules=[ModuleContract(name="domain", paths=["src/domain/**"])],
        commands=ContractCommands(build=[["build-package"]]),
        artifacts=ArtifactContract(required_files=[], required_artifacts=["dist/*.whl"]),
    )

    async def scenario():
        await store.save(active)
        return await service.gate("demo", strict=True, execute_commands=True)

    import asyncio

    report = asyncio.run(scenario())
    assert report.status == "failed"
    assert any(item.gate_id == "artifact-output:dist/*.whl" for item in report.findings)


class PassingContractService:
    async def gate(self, project_id, **kwargs):
        return validate_repository(kwargs["workspace"], contract())


class NoopScheduler:
    async def run(self, *args, **kwargs):
        return ScheduledTests(node_id="test-node", tests=[])


def test_acceptance_records_project_contract_gate_evidence(tmp_path):
    checked = contract()
    repo = repository(
        tmp_path / "accepted",
        {
            "Dockerfile": "HEALTHCHECK /health\n",
            "compose.yaml": "healthcheck: /health\n",
            ".env.example": "MODE=test\n",
            "src/api/handler.py": "from domain.model import value\n",
            "src/domain/model.py": "value = 1\n",
            **render_contract_documents(checked),
        },
    )
    registry = ProjectRegistry(str(tmp_path / "accepted-projects.json"))
    registry.add(ProjectDefinition(id="demo", repository=str(repo)))
    gateway = ProjectAcceptanceGateway(
        registry,
        NoopScheduler(),
        ArtifactStore(str(tmp_path / "artifacts")),
        project_contracts=PassingContractService(),
    )
    implementation = ExecutionResult(
        summary="done",
        workspace=Workspace(project_id="demo", path=str(repo), branch="task", base_commit="a" * 40),
        commit=git(repo, "rev-parse", "HEAD").stdout.strip(),
    )

    import asyncio

    result = asyncio.run(gateway.verify("run-1", "demo", implementation))
    assert result.evidence[0].id == "project-contract-gates"
    assert result.evidence[0].artifacts[0].kind == "architecture_compliance"
