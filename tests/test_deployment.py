import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from taskhub_v2.config import Settings
from taskhub_v2.deployment import runner
from taskhub_v2.deployment.manager import DeploymentManager
from taskhub_v2.domain.models import ProjectDefinition, PublicationResult


def git(path: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def deployment_fixture(tmp_path: Path) -> tuple[argparse.Namespace, Path, Path]:
    authority = tmp_path / "authority.git"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(authority)],
        check=True,
        capture_output=True,
    )
    repository = tmp_path / "repository"
    subprocess.run(
        ["git", "clone", str(authority), str(repository)],
        check=True,
        capture_output=True,
    )
    git(repository, "config", "user.name", "Test")
    git(repository, "config", "user.email", "test@local")
    (repository / "new.txt").write_text("release\n", encoding="utf-8")
    git(repository, "add", "new.txt")
    git(repository, "commit", "-m", "release")
    git(repository, "push", "-u", "origin", "main")
    commit = git(repository, "rev-parse", "HEAD")

    target = tmp_path / "target"
    (target / ".venv").mkdir(parents=True)
    (target / ".env").write_text("SECRET=preserved\n", encoding="utf-8")
    (target / "old.txt").write_text("old\n", encoding="utf-8")
    state = tmp_path / "state" / "deployment.json"
    arguments = argparse.Namespace(
        deployment_id="deploy-test",
        project_id="project-test",
        repository=str(repository),
        remote="origin",
        branch="main",
        commit=commit,
        tests_json=json.dumps([[sys.executable, "-c", "print('ok')"]]),
        target=str(target),
        service="test.service",
        state_file=str(state),
        health_url="http://127.0.0.1/health",
    )
    return arguments, target, state


def test_deployment_replaces_code_and_preserves_runtime_files(tmp_path: Path, monkeypatch):
    arguments, target, state = deployment_fixture(tmp_path)
    monkeypatch.setattr(runner, "service_restart", lambda service: None)
    monkeypatch.setattr(runner, "wait_for_health", lambda url: None)

    runner.deploy(arguments)

    assert (target / "new.txt").read_text() == "release\n"
    assert not (target / "old.txt").exists()
    assert (target / ".env").read_text() == "SECRET=preserved\n"
    assert (target / ".venv").is_dir()
    assert (target / ".taskhub-release").read_text().strip() == arguments.commit
    assert json.loads(state.read_text())["status"] == "completed"


def test_failed_health_check_restores_previous_release(tmp_path: Path, monkeypatch):
    arguments, target, state = deployment_fixture(tmp_path)
    checks = iter([RuntimeError("new release unhealthy"), None])
    monkeypatch.setattr(runner, "service_restart", lambda service: None)

    def health(url):
        result = next(checks)
        if result:
            raise result

    monkeypatch.setattr(runner, "wait_for_health", health)

    with pytest.raises(RuntimeError, match="new release unhealthy"):
        runner.deploy(arguments)

    assert (target / "old.txt").read_text() == "old\n"
    assert not (target / "new.txt").exists()
    assert not (target / ".taskhub-release").exists()
    assert (target / ".env").read_text() == "SECRET=preserved\n"
    status = json.loads(state.read_text())
    assert status["status"] == "rolled_back"
    assert "new release unhealthy" in status["detail"]


def test_manager_launches_independent_systemd_unit(tmp_path: Path, monkeypatch):
    launched = []

    class Process:
        returncode = 0

        async def communicate(self):
            return b"started", b""

    async def launch(*command, **kwargs):
        launched.append(command)
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    settings = Settings(
        self_deploy_enabled=True,
        self_deploy_project_id="project-test",
        self_deploy_target=str(tmp_path / "target"),
        self_deploy_state_file=str(tmp_path / "deployment.json"),
        provider_workdir=str(tmp_path / "application"),
    )
    manager = DeploymentManager(settings)
    project = ProjectDefinition(
        id="project-test",
        repository=str(tmp_path / "repository"),
        authority_remote="origin",
        test_commands=[["python3", "-m", "pytest", "-q"]],
    )
    publication = PublicationResult(
        project_id=project.id,
        authority_ref="origin/main",
        previous_commit="1" * 40,
        published_commit="2" * 40,
        branch="taskhub/run",
    )

    result = asyncio.run(manager.start(project, publication))

    assert result["status"] == "queued"
    assert launched[0][0:3] == ("systemd-run", "--user", "--collect")
    assert any(argument.startswith("--setenv=PYTHONPATH=") for argument in launched[0])
