import asyncio
import hashlib
import json
import subprocess

import pytest

from taskhub_v2.artifacts import ArtifactStore
from taskhub_v2.domain.models import (
    ExecutionResult,
    ScheduledTests,
    Workspace,
)
from taskhub_v2.domain.models import (
    TestExecution as CommandExecution,
)
from taskhub_v2.projects import ProjectRegistry
from taskhub_v2.workers.acceptance import (
    AcceptanceExecutionError,
    ProjectAcceptanceGateway,
)


class RecordingScheduler:
    def __init__(self, exit_code=0):
        self.exit_code = exit_code
        self.calls = []

    async def run(self, job_id, sticky_key, commands, timeout, workdir, workload="test", **kwargs):
        self.calls.append((commands, workload, workdir, kwargs))
        content = b"<testsuite tests='1' failures='0'/>"
        downloaded = [
            {
                "path": path,
                "content": content,
                "sha256": hashlib.sha256(content).hexdigest(),
            }
            for path in kwargs.get("artifact_paths", [])
        ]
        return ScheduledTests(
            node_id="acceptance-node",
            tests=[
                CommandExecution(
                    command=commands[0],
                    exit_code=self.exit_code,
                    output_tail="passed" if not self.exit_code else "failed",
                )
            ],
            metadata={
                "git_commit": kwargs.get("git_commit", ""),
                "downloaded_artifacts": downloaded,
            },
        )


def gateway(
    tmp_path,
    exit_code=0,
    *,
    test_database=False,
    windows_suite=False,
    windows_browser=False,
    unbound_windows=False,
):
    repository = tmp_path / "repo"
    repository.mkdir()
    projects_file = tmp_path / "projects.json"
    projects_file.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "id": "shop",
                        "repository": str(repository),
                        "acceptance_commands": [["python3", "accept.py"]],
                        "acceptance_capabilities": (
                            ["test_database"] if test_database else []
                        ),
                        "test_environment": {
                            "target_url": "https://192.168.31.55",
                            "edge_host": "192.168.31.55",
                            "origin_host": "192.168.31.56",
                        },
                        "windows_test_suite": (
                            {
                                "commands": [["powershell", "-File", "windows-test.ps1"]],
                                "node_ids": [] if unbound_windows else ["windows-01"],
                                "required_capabilities": (
                                    ["windows_gui", "playwright"]
                                    if windows_browser
                                    else ["windows_gui"]
                                ),
                                "artifact_paths": ["test-results/junit.xml"],
                            }
                            if windows_suite
                            else None
                        ),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    scheduler = RecordingScheduler(exit_code)
    return (
        ProjectAcceptanceGateway(
            ProjectRegistry(str(projects_file)),
            scheduler,
            ArtifactStore(str(tmp_path / "artifacts")),
        ),
        scheduler,
        repository,
    )


def implementation(repository, *, commit=None):
    return ExecutionResult(
        summary="done",
        workspace=Workspace(
            project_id="shop", path=str(repository), branch="task", base_commit="abc"
        ),
        commit=commit,
    )


def test_project_acceptance_runs_as_separate_workload_and_records_artifact(tmp_path):
    worker, scheduler, repository = gateway(tmp_path)
    result = asyncio.run(worker.verify("run-1", "shop", implementation(repository)))

    assert result.status == "passed"
    assert result.evidence[0].source == "acceptance-node"
    assert result.evidence[0].artifacts[0].kind == "acceptance_report"
    assert scheduler.calls == [
        (
            [["python3", "accept.py"]],
            "acceptance",
            str(repository),
            {
                "required_capabilities_override": set(),
                "execution_environment": {
                    "TASKHUB_TEST_TARGET_URL": "https://192.168.31.55",
                    "TASKHUB_TEST_EDGE_HOST": "192.168.31.55",
                    "TASKHUB_TEST_ORIGIN_HOST": "192.168.31.56",
                    "TASKHUB_TEST_EXPECTED_ENVIRONMENT": "production",
                    "TASKHUB_TEST_ENVIRONMENT_PROFILE": "dedicated",
                },
            },
        )
    ]


def test_failed_acceptance_blocks_the_workflow(tmp_path):
    worker, _, repository = gateway(tmp_path, exit_code=1)

    with pytest.raises(AcceptanceExecutionError, match="failed"):
        asyncio.run(worker.verify("run-1", "shop", implementation(repository)))


def test_successful_project_acceptance_is_reused_for_unchanged_retry(tmp_path):
    worker, scheduler, repository = gateway(tmp_path)
    candidate = implementation(repository, commit="a" * 40)

    first = asyncio.run(worker.verify("run-retry", "shop", candidate))
    second = asyncio.run(worker.verify("run-retry", "shop", candidate))

    assert len(scheduler.calls) == 1
    assert first.evidence[0].summary == "1/1 acceptance commands passed"
    assert "reused verified checkpoint" in second.evidence[0].summary


def test_project_acceptance_checkpoint_is_invalidated_by_candidate_change(tmp_path):
    worker, scheduler, repository = gateway(tmp_path)

    asyncio.run(
        worker.verify(
            "run-retry", "shop", implementation(repository, commit="a" * 40)
        )
    )
    asyncio.run(
        worker.verify(
            "run-retry", "shop", implementation(repository, commit="b" * 40)
        )
    )

    assert len(scheduler.calls) == 2


def test_project_acceptance_checkpoint_ignores_only_browser_contract_change(tmp_path):
    worker, scheduler, repository = gateway(tmp_path)
    subprocess.run(["git", "init"], cwd=repository, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "taskhub@test.invalid"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "TaskHub Test"], cwd=repository, check=True
    )
    (repository / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    contract = repository / ".taskhub" / "acceptance.yaml"
    contract.parent.mkdir()
    contract.write_text("command: [playwright, test]\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(
        ["git", "commit", "-m", "base"],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    first_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repository, text=True
    ).strip()

    asyncio.run(
        worker.verify(
            "run-contract", "shop", implementation(repository, commit=first_commit)
        )
    )
    contract.write_text("command: [playwright, test, --workers=1]\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(
        ["git", "commit", "-m", "browser contract only"],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    second_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repository, text=True
    ).strip()

    second = asyncio.run(
        worker.verify("run-contract", "shop", implementation(repository, commit=second_commit))
    )

    assert len(scheduler.calls) == 1
    assert "unchanged project acceptance scope" in second.evidence[0].summary


def test_database_acceptance_records_isolated_database_evidence(tmp_path):
    worker, scheduler, repository = gateway(tmp_path, test_database=True)

    result = asyncio.run(worker.verify("run-db", "shop", implementation(repository)))

    database = next(item for item in result.evidence if item.kind == "database")
    assert database.id == "project-acceptance-database"
    assert database.status == "passed"
    assert database.source == "acceptance-node"
    assert "job-isolated test database" in database.summary
    assert scheduler.calls[0][3]["required_capabilities_override"] == {"test_database"}


def test_windows_test_suite_targets_configured_node_and_records_evidence(tmp_path):
    worker, scheduler, repository = gateway(tmp_path, windows_suite=True)

    result = asyncio.run(
        worker.verify("run-windows", "shop", implementation(repository, commit="abc123"))
    )

    windows = next(item for item in result.evidence if item.id == "project-windows-test-suite")
    assert windows.status == "passed"
    assert windows.source == "acceptance-node"
    assert windows.artifacts[0].kind == "junit_report"
    assert scheduler.calls[0] == (
        [["powershell", "-File", "windows-test.ps1"]],
        "acceptance",
        str(repository),
        {
            "required_capabilities_override": {"windows_gui"},
            "eligible_node_ids": {"windows-01"},
            "git_commit": "abc123",
            "artifact_paths": ["test-results/junit.xml"],
            "execution_environment": {
                "TASKHUB_TEST_TARGET_URL": "https://192.168.31.55",
                "TASKHUB_TEST_EDGE_HOST": "192.168.31.55",
                "TASKHUB_TEST_ORIGIN_HOST": "192.168.31.56",
                "TASKHUB_TEST_EXPECTED_ENVIRONMENT": "production",
                "TASKHUB_TEST_ENVIRONMENT_PROFILE": "dedicated",
            },
        },
    )


def test_windows_test_suite_refuses_unbound_global_node_fallback(tmp_path):
    worker, scheduler, repository = gateway(
        tmp_path, windows_suite=True, unbound_windows=True
    )

    with pytest.raises(
        AcceptanceExecutionError,
        match="未显式绑定当前项目获授权的节点",
    ):
        asyncio.run(
            worker.verify(
                "run-windows-unbound",
                "shop",
                implementation(repository, commit="abc123"),
            )
        )

    assert scheduler.calls == []


def test_browser_windows_suite_uses_browser_acceptance_nodes(tmp_path):
    worker, scheduler, repository = gateway(
        tmp_path, windows_suite=True, windows_browser=True
    )

    result = asyncio.run(
        worker.verify("run-browser", "shop", implementation(repository, commit="abc123"))
    )

    windows = next(item for item in result.evidence if item.id == "project-windows-test-suite")
    assert windows.kind == "browser"
    assert scheduler.calls[0][1] == "browser_acceptance"
