import asyncio
import subprocess
from pathlib import Path

from taskhub_v2.domain.models import ProjectDefinition
from taskhub_v2.git import GitWorkspaceManager


def git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def test_worktree_is_isolated_and_idempotent(tmp_path: Path):
    repository = tmp_path / "authority"
    repository.mkdir()
    git(repository, "init", "-b", "main")
    git(repository, "config", "user.email", "test@taskhub.local")
    git(repository, "config", "user.name", "TaskHub Test")
    (repository / "README.md").write_text("authority\n", encoding="utf-8")
    git(repository, "add", "README.md")
    git(repository, "commit", "-m", "initial")
    project = ProjectDefinition(id="demo", repository=str(repository))
    manager = GitWorkspaceManager(str(tmp_path / "workspaces"))

    first = asyncio.run(manager.prepare(project, "run-123"))
    second = asyncio.run(manager.prepare(project, "run-123"))

    assert first == second
    assert Path(first.path).is_dir()
    assert Path(first.path) != repository
    assert git(Path(first.path), "branch", "--show-current") == "taskhub/run-123"
    assert first.base_commit == git(repository, "rev-parse", "main")


def test_existing_worktree_keeps_branch_base_when_authority_advances(tmp_path: Path):
    repository = tmp_path / "authority"
    repository.mkdir()
    git(repository, "init", "-b", "main")
    git(repository, "config", "user.email", "test@taskhub.local")
    git(repository, "config", "user.name", "TaskHub Test")
    (repository / "README.md").write_text("authority\n", encoding="utf-8")
    git(repository, "add", "README.md")
    git(repository, "commit", "-m", "initial")
    project = ProjectDefinition(id="demo", repository=str(repository))
    manager = GitWorkspaceManager(str(tmp_path / "workspaces"))
    first = asyncio.run(manager.prepare(project, "run-123"))

    (repository / "other.txt").write_text("another line\n", encoding="utf-8")
    git(repository, "add", "other.txt")
    git(repository, "commit", "-m", "advance authority")
    second = asyncio.run(manager.prepare(project, "run-123"))

    assert second.base_commit == first.base_commit
    assert second.base_commit != git(repository, "rev-parse", "main")


def test_existing_worktree_keeps_original_base_after_branch_enters_main(tmp_path: Path):
    repository = tmp_path / "authority"
    repository.mkdir()
    git(repository, "init", "-b", "main")
    git(repository, "config", "user.email", "test@taskhub.local")
    git(repository, "config", "user.name", "TaskHub Test")
    (repository / "README.md").write_text("authority\n", encoding="utf-8")
    git(repository, "add", "README.md")
    git(repository, "commit", "-m", "initial")
    initial = git(repository, "rev-parse", "HEAD")
    project = ProjectDefinition(id="demo", repository=str(repository))
    manager = GitWorkspaceManager(str(tmp_path / "workspaces"))
    workspace = asyncio.run(manager.prepare(project, "run-123"))
    worktree = Path(workspace.path)
    (worktree / "feature.txt").write_text("implemented\n", encoding="utf-8")
    git(worktree, "add", "feature.txt")
    git(
        worktree,
        "commit",
        "-m",
        "taskhub: implement feature",
        "-m",
        "TaskHub-Run: run-123\nTaskHub-Revision: 0",
    )
    git(repository, "merge", "--ff-only", "taskhub/run-123")

    restored = asyncio.run(manager.prepare(project, "run-123"))

    assert restored.base_commit == initial
    assert restored.base_commit != git(repository, "rev-parse", "main")
