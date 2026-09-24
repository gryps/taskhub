import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from taskhub_v2.artifacts import ArtifactStore
from taskhub_v2.domain.models import CodeChangeSummary, ModelResult, Plan
from taskhub_v2.git import GitWorkspaceManager
from taskhub_v2.projects import ProjectRegistry
from taskhub_v2.workers.git_coder import GitCodingWorker, WorkerExecutionError


def git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


class FileWritingCoder:
    def __init__(self):
        self.calls = 0

    async def modify(
        self, requirement: str, plan: Plan, workdir: str, feedback: str = ""
    ):
        self.calls += 1
        Path(workdir, "feature.txt").write_text("implemented\n", encoding="utf-8")
        return ModelResult(
            content=CodeChangeSummary(summary="Add feature", tests=[]),
            provider="fake_coder",
            model="test",
        )


class PythonWritingCoder(FileWritingCoder):
    async def modify(
        self, requirement: str, plan: Plan, workdir: str, feedback: str = ""
    ):
        self.calls += 1
        Path(workdir, "feature.py").write_text("value = 1\n", encoding="utf-8")
        return ModelResult(
            content=CodeChangeSummary(summary="Add Python feature", tests=[]),
            provider="fake_coder",
            model="test",
        )


class NoChangeCoder(FileWritingCoder):
    async def modify(self, requirement, plan, workdir, feedback=""):
        self.calls += 1
        return ModelResult(
            content=CodeChangeSummary(summary="Validated existing implementation", tests=[]),
            provider="fake_coder",
            model="test",
        )


class WriteOnRetryCoder(NoChangeCoder):
    async def modify(self, requirement, plan, workdir, feedback=""):
        self.calls += 1
        if self.calls == 2:
            Path(workdir, "feature.txt").write_text("implemented\n", encoding="utf-8")
            summary = "Implemented after inspecting the repository"
        else:
            summary = "Validated existing implementation"
        return ModelResult(
            content=CodeChangeSummary(summary=summary, tests=[]),
            provider="fake_coder",
            model="test",
        )


class FixAfterRetestCoder(FileWritingCoder):
    def __init__(self):
        super().__init__()
        self.feedback = []

    async def modify(self, requirement, plan, workdir, feedback=""):
        self.calls += 1
        self.feedback.append(feedback)
        value = "broken\n" if self.calls == 1 else "fixed\n"
        Path(workdir, "feature.txt").write_text(value, encoding="utf-8")
        return ModelResult(
            content=CodeChangeSummary(summary="Repair feature", tests=[]),
            provider="fake_coder",
            model="test",
        )


class NoChangeThenFixRetestCoder(FixAfterRetestCoder):
    async def modify(self, requirement, plan, workdir, feedback=""):
        self.calls += 1
        self.feedback.append(feedback)
        if self.calls == 1:
            Path(workdir, "feature.txt").write_text("broken\n", encoding="utf-8")
            summary = "Created initial implementation"
        elif self.calls == 2:
            summary = "Validated existing implementation"
        else:
            Path(workdir, "feature.txt").write_text("fixed\n", encoding="utf-8")
            summary = "Repaired failing implementation"
        return ModelResult(
            content=CodeChangeSummary(summary=summary, tests=[]),
            provider="fake_coder",
            model="test",
        )


class RecordingScheduler:
    def __init__(self, delegate):
        self.delegate = delegate
        self.job_ids = []

    async def run(self, job_id, *args, **kwargs):
        self.job_ids.append(job_id)
        return await self.delegate.run(job_id, *args, **kwargs)


def test_git_worker_changes_tests_artifacts_and_commits(tmp_path: Path):
    repository = tmp_path / "authority"
    repository.mkdir()
    git(repository, "init", "-b", "main")
    git(repository, "config", "user.email", "test@taskhub.local")
    git(repository, "config", "user.name", "TaskHub Test")
    (repository / "README.md").write_text("authority\n", encoding="utf-8")
    git(repository, "add", "README.md")
    git(repository, "commit", "-m", "initial")
    projects_file = tmp_path / "projects.json"
    projects_file.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "id": "demo",
                        "repository": str(repository),
                        "test_commands": [
                            [
                                "python3",
                                "-c",
                                "from pathlib import Path; "
                                "assert Path('feature.txt').read_text() == 'implemented\\n'",
                            ]
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    coder = FileWritingCoder()
    worker = GitCodingWorker(
        ProjectRegistry(str(projects_file)),
        GitWorkspaceManager(str(tmp_path / "workspaces")),
        coder,
        ArtifactStore(str(tmp_path / "artifacts")),
    )
    plan = Plan(summary="Plan", steps=["Add file"], acceptance=["Test passes"])

    first = asyncio.run(worker.execute("run-1", "demo", "Add feature", plan))
    second = asyncio.run(worker.execute("run-1", "demo", "Add feature", plan))

    assert first.commit
    assert first.changed_files == ["feature.txt"]
    assert first.tests[0].exit_code == 0
    assert first.artifacts[0].sha256
    assert "feature.txt" in first.evidence
    assert git(repository, "show", "main:README.md") == "authority"
    assert coder.calls == 1
    assert second.commit == first.commit


def test_git_worker_retests_uncommitted_changes_before_revision_coding(tmp_path: Path):
    repository = tmp_path / "authority"
    repository.mkdir()
    git(repository, "init", "-b", "main")
    git(repository, "config", "user.email", "test@taskhub.local")
    git(repository, "config", "user.name", "TaskHub Test")
    (repository / "README.md").write_text("authority\n", encoding="utf-8")
    git(repository, "add", "README.md")
    git(repository, "commit", "-m", "initial")
    gate = tmp_path / "registry-online"
    projects_file = tmp_path / "projects.json"
    projects_file.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "id": "demo",
                        "repository": str(repository),
                        "test_commands": [
                            [
                                "python3",
                                "-c",
                                "from pathlib import Path; "
                                f"assert Path({str(gate)!r}).exists()",
                            ]
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    coder = FileWritingCoder()
    worker = GitCodingWorker(
        ProjectRegistry(str(projects_file)),
        GitWorkspaceManager(str(tmp_path / "workspaces")),
        coder,
        ArtifactStore(str(tmp_path / "artifacts")),
    )
    scheduler = RecordingScheduler(worker.test_scheduler)
    worker.test_scheduler = scheduler
    plan = Plan(summary="Plan", steps=["Add file"], acceptance=["Test passes"])

    try:
        asyncio.run(worker.execute("run-retest", "demo", "Add feature", plan))
    except WorkerExecutionError as exc:
        assert exc.reason == "tests_failed"
    else:
        raise AssertionError("the initial transient test failure must block the run")
    gate.write_text("online\n", encoding="utf-8")

    recovered = asyncio.run(
        worker.execute(
            "run-retest",
            "demo",
            "Add feature",
            plan,
            revision=1,
            feedback="npm registry timed out",
        )
    )

    assert coder.calls == 1
    assert recovered.changed_files == ["feature.txt"]
    assert recovered.coding_node == "workspace-recovery"
    assert recovered.tests[0].exit_code == 0
    assert scheduler.job_ids == ["run-retest-r0", "run-retest-r1"]
    message = git(Path(recovered.workspace.path), "log", "-1", "--format=%B")
    assert "TaskHub-Recovery: quality-retest" in message


def test_failed_retest_feedback_reaches_revision_coder(tmp_path: Path):
    repository = tmp_path / "authority"
    repository.mkdir()
    git(repository, "init", "-b", "main")
    git(repository, "config", "user.email", "test@taskhub.local")
    git(repository, "config", "user.name", "TaskHub Test")
    (repository / "README.md").write_text("authority\n", encoding="utf-8")
    git(repository, "add", "README.md")
    git(repository, "commit", "-m", "initial")
    projects_file = tmp_path / "projects.json"
    projects_file.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "id": "demo",
                        "repository": str(repository),
                        "test_commands": [
                            [
                                "python3",
                                "-c",
                                "from pathlib import Path; "
                                "assert Path('feature.txt').read_text() == 'fixed\\n'",
                            ]
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    coder = FixAfterRetestCoder()
    worker = GitCodingWorker(
        ProjectRegistry(str(projects_file)),
        GitWorkspaceManager(str(tmp_path / "workspaces")),
        coder,
        ArtifactStore(str(tmp_path / "artifacts")),
    )
    plan = Plan(summary="Plan", steps=["Repair"], acceptance=["Test passes"])

    with pytest.raises(WorkerExecutionError, match="feature.txt"):
        asyncio.run(worker.execute("run-feedback", "demo", "Repair feature", plan))
    result = asyncio.run(
        worker.execute(
            "run-feedback",
            "demo",
            "Repair feature",
            plan,
            revision=1,
            feedback="Previous execution infrastructure failed",
        )
    )

    assert result.tests[0].exit_code == 0
    assert coder.calls == 2
    assert "Latest quality retest failed" in coder.feedback[1]
    assert "feature.txt" in coder.feedback[1]


def test_failed_retest_does_not_accept_empty_model_change_over_dirty_worktree(tmp_path: Path):
    repository = tmp_path / "authority"
    repository.mkdir()
    git(repository, "init", "-b", "main")
    git(repository, "config", "user.email", "test@taskhub.local")
    git(repository, "config", "user.name", "TaskHub Test")
    (repository / "feature.txt").write_text("base\n", encoding="utf-8")
    git(repository, "add", "feature.txt")
    git(repository, "commit", "-m", "initial")
    projects_file = tmp_path / "projects.json"
    projects_file.write_text(
        json.dumps({"projects": [{
            "id": "demo",
            "repository": str(repository),
            "test_commands": [[
                "python3", "-c",
                "from pathlib import Path; "
                "assert Path('feature.txt').read_text() == 'fixed\\n'",
            ]],
        }]}),
        encoding="utf-8",
    )
    coder = NoChangeThenFixRetestCoder()
    worker = GitCodingWorker(
        ProjectRegistry(str(projects_file)),
        GitWorkspaceManager(str(tmp_path / "workspaces")),
        coder,
        ArtifactStore(str(tmp_path / "artifacts")),
    )
    plan = Plan(summary="Plan", steps=["Repair"], acceptance=["Test passes"])

    with pytest.raises(WorkerExecutionError):
        asyncio.run(worker.execute("run-empty-repair", "demo", "Repair", plan))
    result = asyncio.run(worker.execute(
        "run-empty-repair", "demo", "Repair", plan, revision=1,
        feedback="Run the formatter fix",
    ))

    assert result.tests[0].exit_code == 0
    assert coder.calls == 3
    assert "Latest quality retest failed" in coder.feedback[1]
    assert "produced no Git changes" in coder.feedback[2]


def test_git_worker_does_not_commit_test_generated_files(tmp_path: Path):
    repository = tmp_path / "authority"
    repository.mkdir()
    git(repository, "init", "-b", "main")
    git(repository, "config", "user.email", "test@taskhub.local")
    git(repository, "config", "user.name", "TaskHub Test")
    (repository / "README.md").write_text("authority\n", encoding="utf-8")
    git(repository, "add", "README.md")
    git(repository, "commit", "-m", "initial")
    projects_file = tmp_path / "projects.json"
    projects_file.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "id": "demo",
                        "repository": str(repository),
                        "test_commands": [["python3", "-c", "import feature"]],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    worker = GitCodingWorker(
        ProjectRegistry(str(projects_file)),
        GitWorkspaceManager(str(tmp_path / "workspaces")),
        PythonWritingCoder(),
        ArtifactStore(str(tmp_path / "artifacts")),
    )

    result = asyncio.run(
        worker.execute(
            "run-python",
            "demo",
            "Add feature",
            Plan(summary="Plan", steps=["code"], acceptance=["pass"]),
        )
    )

    committed = git(Path(result.workspace.path), "show", "--name-only", "--format=", "HEAD")
    assert committed == "feature.py"


def test_git_worker_creates_and_recovers_revision_commit(tmp_path: Path):
    repository = tmp_path / "authority"
    repository.mkdir()
    git(repository, "init", "-b", "main")
    git(repository, "config", "user.email", "test@taskhub.local")
    git(repository, "config", "user.name", "TaskHub Test")
    (repository / "feature.txt").write_text("base\n", encoding="utf-8")
    git(repository, "add", "feature.txt")
    git(repository, "commit", "-m", "initial")
    projects_file = tmp_path / "projects.json"
    projects_file.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "id": "demo",
                        "repository": str(repository),
                        "test_commands": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    class RevisionCoder(FileWritingCoder):
        async def modify(self, requirement, plan, workdir, feedback=""):
            self.calls += 1
            content = "initial\n" if not feedback else "initial\nfixed\n"
            Path(workdir, "feature.txt").write_text(content, encoding="utf-8")
            return ModelResult(
                content=CodeChangeSummary(summary="Revise feature", tests=[]),
                provider="fake_coder",
                model="test",
            )

    coder = RevisionCoder()
    worker = GitCodingWorker(
        ProjectRegistry(str(projects_file)),
        GitWorkspaceManager(str(tmp_path / "workspaces")),
        coder,
        ArtifactStore(str(tmp_path / "artifacts")),
    )
    plan = Plan(summary="Plan", steps=["code"], acceptance=["pass"])

    first = asyncio.run(worker.execute("run-revision", "demo", "Change", plan))
    revised = asyncio.run(
        worker.execute(
            "run-revision", "demo", "Change", plan, revision=1, feedback="Fix it"
        )
    )
    recovered = asyncio.run(
        worker.execute(
            "run-revision", "demo", "Change", plan, revision=1, feedback="Fix it"
        )
    )

    assert first.commit != revised.commit
    assert revised.commit == recovered.commit
    assert coder.calls == 2
    assert "initial" in revised.evidence
    assert "fixed" in revised.evidence
    message = git(Path(revised.workspace.path), "log", "-1", "--format=%B")
    assert "TaskHub-Revision: 1" in message
    assert "TaskHub-Feedback:" in message


def test_git_worker_accepts_evidence_only_revision_but_not_empty_initial_work(tmp_path: Path):
    repository = tmp_path / "authority"
    repository.mkdir()
    git(repository, "init", "-b", "main")
    git(repository, "config", "user.email", "test@taskhub.local")
    git(repository, "config", "user.name", "TaskHub Test")
    (repository / "feature.txt").write_text("base\n", encoding="utf-8")
    git(repository, "add", "feature.txt")
    git(repository, "commit", "-m", "initial")
    projects_file = tmp_path / "projects.json"
    projects_file.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "id": "demo",
                        "repository": str(repository),
                        "test_commands": [["python3", "-c", "assert True"]],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    plan = Plan(summary="Plan", steps=["validate"], acceptance=["tests pass"])
    workspaces = GitWorkspaceManager(str(tmp_path / "workspaces"))
    initial_worker = GitCodingWorker(
        ProjectRegistry(str(projects_file)), workspaces, NoChangeCoder(),
        ArtifactStore(str(tmp_path / "artifacts")),
    )

    try:
        asyncio.run(initial_worker.execute("empty", "demo", "Do work", plan))
    except WorkerExecutionError as exc:
        assert exc.reason == "no_changes"
        assert len(exc.model_results) == 2
        assert "fake_coder/test" in exc.detail
        assert "Validated existing implementation" in exc.detail
    else:
        raise AssertionError("initial no-change implementation must be blocked")

    retry_worker = GitCodingWorker(
        ProjectRegistry(str(projects_file)), GitWorkspaceManager(str(tmp_path / "retry")),
        WriteOnRetryCoder(), ArtifactStore(str(tmp_path / "retry-artifacts")),
    )
    retried = asyncio.run(retry_worker.execute("retry", "demo", "Do work", plan))
    assert retried.changed_files == ["feature.txt"]
    assert retried.commit

    writing_worker = GitCodingWorker(
        ProjectRegistry(str(projects_file)), workspaces, FileWritingCoder(),
        ArtifactStore(str(tmp_path / "artifacts")),
    )
    first = asyncio.run(writing_worker.execute("evidence", "demo", "Do work", plan))
    evidence_coder = NoChangeCoder()
    evidence_worker = GitCodingWorker(
        ProjectRegistry(str(projects_file)), workspaces, evidence_coder,
        ArtifactStore(str(tmp_path / "artifacts")),
    )
    revised = asyncio.run(
        evidence_worker.execute(
            "evidence",
            "demo",
            "Do work",
            plan,
            revision=1,
            feedback="Provide test evidence",
        )
    )

    assert revised.commit == first.commit
    assert evidence_coder.calls == 1
    assert revised.tests[0].exit_code == 0
    assert revised.changed_files == ["feature.txt"]
    assert revised.artifacts[0].sha256
