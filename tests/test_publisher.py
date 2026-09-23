import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from taskhub_v2.domain.models import (
    ExecutionResult,
    TestExecution as ExecutionTestResult,
    Workspace,
)
from taskhub_v2.projects import ProjectRegistry
from taskhub_v2.workers.publisher import GitPublisher, PublicationError


def git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def setup_project(tmp_path: Path):
    repository = tmp_path / "repository"
    repository.mkdir()
    git(repository, "init", "-b", "main")
    git(repository, "config", "user.name", "Tests")
    git(repository, "config", "user.email", "tests@example.test")
    (repository / "value.txt").write_text("base\n", encoding="utf-8")
    git(repository, "add", "value.txt")
    git(repository, "commit", "-m", "base")
    base = git(repository, "rev-parse", "HEAD")

    workspace_root = tmp_path / "workspaces"
    run_id = "run-1"
    worktree = workspace_root / "demo" / run_id
    worktree.parent.mkdir(parents=True)
    branch = f"taskhub/{run_id}"
    git(repository, "worktree", "add", "-b", branch, str(worktree), base)
    (worktree / "value.txt").write_text("changed\n", encoding="utf-8")
    git(worktree, "add", "value.txt")
    git(worktree, "commit", "-m", "change")
    commit = git(worktree, "rev-parse", "HEAD")

    config = tmp_path / "projects.json"
    config.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "id": "demo",
                        "repository": str(repository),
                        "base_ref": "main",
                        "test_commands": [["git", "diff", "--quiet"]],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    implementation = ExecutionResult(
        summary="changed",
        workspace=Workspace(project_id="demo", path=str(worktree), branch=branch, base_commit=base),
        commit=commit,
        changed_files=["value.txt"],
    )
    return repository, workspace_root, config, implementation


def test_publisher_fast_forwards_authority(tmp_path: Path):
    repository, root, config, implementation = setup_project(tmp_path)
    publisher = GitPublisher(ProjectRegistry(str(config)), str(root))

    result = asyncio.run(publisher.publish("run-1", "demo", implementation))

    assert result.published_commit == implementation.commit
    assert git(repository, "rev-parse", "main") == implementation.commit
    assert result.rebased is False
    assert result.verification_reused is False


def test_publisher_reuses_exact_passing_implementation_verification(tmp_path: Path):
    repository, root, config, implementation = setup_project(tmp_path)
    implementation.tests = [
        ExecutionTestResult(command=["git", "diff", "--quiet"], exit_code=0, output_tail="passed")
    ]
    implementation.execution_node = "test-01"

    class UnexpectedScheduler:
        async def run(self, *args, **kwargs):
            raise AssertionError("unchanged verified commit must not rerun publication tests")

    publisher = GitPublisher(
        ProjectRegistry(str(config)), str(root), test_scheduler=UnexpectedScheduler()
    )
    result = asyncio.run(publisher.publish("run-1", "demo", implementation))

    assert result.published_commit == implementation.commit
    assert result.verification_reused is True
    assert result.tests == implementation.tests
    assert result.execution_node == "test-01"
    assert git(repository, "rev-parse", "main") == implementation.commit


@pytest.mark.parametrize(
    "tests",
    [
        [],
        [ExecutionTestResult(command=["git", "status", "--short"], exit_code=0, output_tail="")],
        [
            ExecutionTestResult(
                command=["git", "diff", "--quiet"], exit_code=1, output_tail="failed"
            )
        ],
    ],
)
def test_publisher_reruns_when_evidence_is_missing_changed_or_failed(tmp_path: Path, tests):
    _, root, config, implementation = setup_project(tmp_path)
    implementation.tests = tests
    publisher = GitPublisher(ProjectRegistry(str(config)), str(root))

    result = asyncio.run(publisher.publish("run-1", "demo", implementation))

    assert result.verification_reused is False
    assert [test.command for test in result.tests] == [["git", "diff", "--quiet"]]
    assert all(test.exit_code == 0 for test in result.tests)


def test_publisher_rejects_change_after_supervision(tmp_path: Path):
    _, root, config, implementation = setup_project(tmp_path)
    Path(implementation.workspace.path, "later.txt").write_text("later\n", encoding="utf-8")
    git(Path(implementation.workspace.path), "add", "later.txt")
    git(Path(implementation.workspace.path), "commit", "-m", "later")
    publisher = GitPublisher(ProjectRegistry(str(config)), str(root))

    with pytest.raises(PublicationError, match="changed after supervision"):
        asyncio.run(publisher.publish("run-1", "demo", implementation))


def test_publisher_rebases_when_another_line_published_first(tmp_path: Path):
    repository, root, config, implementation = setup_project(tmp_path)
    implementation.tests = [
        ExecutionTestResult(command=["git", "diff", "--quiet"], exit_code=0, output_tail="passed")
    ]
    (repository / "other.txt").write_text("other\n", encoding="utf-8")
    git(repository, "add", "other.txt")
    git(repository, "commit", "-m", "other line")
    previous = git(repository, "rev-parse", "HEAD")
    publisher = GitPublisher(ProjectRegistry(str(config)), str(root))

    result = asyncio.run(publisher.publish("run-1", "demo", implementation))

    assert result.rebased is True
    assert result.verification_reused is False
    assert result.previous_commit == previous
    assert result.published_commit != implementation.commit
    assert git(repository, "rev-parse", "main") == result.published_commit


def test_publisher_pushes_managed_project_to_remote_authority(tmp_path: Path):
    repository, root, config, implementation = setup_project(tmp_path)
    remote = tmp_path / "authority.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    git(repository, "remote", "add", "origin", str(remote))
    git(repository, "push", "-u", "origin", "main")
    payload = json.loads(config.read_text(encoding="utf-8"))
    payload["projects"][0]["authority_remote"] = "origin"
    config.write_text(json.dumps(payload), encoding="utf-8")
    publisher = GitPublisher(ProjectRegistry(str(config)), str(root))

    result = asyncio.run(publisher.publish("run-1", "demo", implementation))

    remote_commit = subprocess.run(
        ["git", "--git-dir", str(remote), "rev-parse", "main"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert remote_commit == result.published_commit
    assert result.authority_ref == "origin/main"
