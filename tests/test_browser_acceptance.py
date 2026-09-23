import asyncio
import hashlib
import json
import sys
from pathlib import Path

import pytest

from taskhub_v2.artifacts.store import ArtifactStore
from taskhub_v2.browser.contract import (
    AcceptanceContractValidationError,
    AcceptanceSuiteValidationError,
    load_acceptance_contract,
    load_acceptance_suite,
)
from taskhub_v2.browser.reports import validate_junit
from taskhub_v2.domain.models import (
    ExecutionResult,
    RunStatus,
    ScheduledTests,
    TestExecution,
    Workspace,
)
from taskhub_v2.node_agent.runtime import normalize_command, run_commands
from taskhub_v2.workflows.acceptance_recovery import prepare_acceptance_revision
from taskhub_v2.workflows.browser_acceptance import (
    request_browser_acceptance,
    route_browser_acceptance,
)


def test_candidate_identity_reaches_real_subprocess(tmp_path):
    results = asyncio.run(run_commands(
        tmp_path, [[sys.executable, "-c",
        "import os; print(os.environ['TASKHUB_TARGET_URL']); "
        "print(os.environ['TASKHUB_GIT_COMMIT'])"]], 10,
        execution_environment={"TASKHUB_TARGET_URL": "http://192.168.31.31:8401",
                               "TASKHUB_GIT_COMMIT": "a" * 40},
    ))
    assert results[0]["exit_code"] == 0
    assert results[0]["output_tail"].splitlines() == [
        "http://192.168.31.31:8401", "a" * 40]


@pytest.mark.parametrize("report", [
    b"<testsuites/>",
    b'<testsuite><testcase><skipped/></testcase></testsuite>',
    b'<testsuite skipped="1"><testcase/></testsuite>',
    b'<testsuite><testcase><failure/></testcase></testsuite>',
    b'<testsuite><testcase/></testsuite>',
    b'<!DOCTYPE x><testsuite><testcase/></testsuite>',
])
def test_junit_rejects_incomplete_or_skipped_evidence(report):
    with pytest.raises(ValueError):
        validate_junit([report], ["chromium", "edge"])


def test_junit_surfaces_browser_failure_detail():
    report = (
        b"<testsuite failures='1'><testcase><failure>"
        b"UnicodeDecodeError: gbk</failure></testcase></testsuite>"
    )
    with pytest.raises(ValueError, match="UnicodeDecodeError: gbk"):
        validate_junit([report], ["chromium"])


def test_junit_strips_ansi_control_sequences_before_reporting_failure():
    report = (
        b'<testsuite tests="1" failures="1" skipped="0" errors="0">'
        b'<testcase><properties><property name="browser" value="chromium"/>'
        b'</properties><failure>\x1b[31mresponsive heading missing\x1b[0m</failure>'
        b'</testcase></testsuite>'
    )

    with pytest.raises(
        ValueError, match="browser acceptance failed: responsive heading missing"
    ):
        validate_junit([report], ["chromium"])


def test_junit_does_not_repair_unrelated_malformed_xml():
    report = b'<testsuite>\x1b[31m<testcase></testsuite>'

    with pytest.raises(ValueError, match="invalid JUnit report"):
        validate_junit([report], ["chromium"])


def test_junit_requires_both_executed_browsers():
    reports = [f'<testsuite><testcase><properties><property name="browser" '
               f'value="{browser}"/></properties></testcase></testsuite>'.encode()
               for browser in ("chromium", "edge")]
    validate_junit(reports, ["chromium", "edge"])
    with pytest.raises(ValueError, match="target_url mismatch"):
        validate_junit(reports, ["chromium", "edge"], target_url="http://candidate:8400")


def test_windows_portable_commands_are_mapped_to_native_executables():
    assert normalize_command(["npm", "test"], windows=True) == ["npm.cmd", "test"]
    assert normalize_command(["npx", "playwright", "test"], windows=True) == [
        "npx.cmd",
        "playwright",
        "test",
    ]
    mapped_python = normalize_command(["python3", "-V"], windows=True)
    assert mapped_python[0] == sys.executable


def test_linux_python_command_uses_node_runtime_interpreter():
    mapped_python = normalize_command(["python3", "-m", "pytest"], windows=False)
    assert mapped_python == [sys.executable, "-m", "pytest"]


def test_acceptance_contract_requires_dedicated_lane_and_browser_capabilities(tmp_path):
    directory = tmp_path / ".taskhub"
    directory.mkdir()
    (directory / "acceptance.yaml").write_text(
        """
preview:
  command: [python3, -m, app, --port, "{port}"]
browsers: [chromium, edge]
command: [npx, playwright, test]
""",
        encoding="utf-8",
    )
    contract = load_acceptance_contract(tmp_path)
    assert contract.workload == "browser_acceptance"
    assert {"chromium", "edge", "windows_gui", "trace"} <= contract.required_capabilities


def test_invalid_acceptance_contract_returns_exact_repair_schema(tmp_path):
    directory = tmp_path / ".taskhub"
    directory.mkdir()
    (directory / "acceptance.yaml").write_text(
        "preview:\n  command: python app.py\nentrypoint: python browser.py\n",
        encoding="utf-8",
    )

    with pytest.raises(AcceptanceContractValidationError) as error:
        load_acceptance_contract(tmp_path)

    assert error.value.reason == "acceptance_contract_invalid"
    assert "preview:\n  command: [python3" in error.value.detail
    assert "command: [npx, playwright, test]" in error.value.detail
    assert "description: Create, refresh and filter tasks" in error.value.detail


def test_preview_contract_requires_allocated_port_placeholder(tmp_path):
    directory = tmp_path / ".taskhub"
    directory.mkdir()
    (directory / "acceptance.yaml").write_text(
        "preview:\n  command: [python3, app.py, --port, '8200']\n"
        "command: [npx, playwright, test]\n",
        encoding="utf-8",
    )

    with pytest.raises(AcceptanceContractValidationError, match="port.*placeholder"):
        load_acceptance_contract(tmp_path)


def test_invalid_acceptance_suite_returns_exact_repair_schema(tmp_path):
    suite = tmp_path / "suite.yaml"
    suite.write_text(
        "scenarios:\n  - id: task-list\n    checks: [create, refresh]\n",
        encoding="utf-8",
    )

    contract = type("Contract", (), {"suite": "suite.yaml"})()
    with pytest.raises(AcceptanceSuiteValidationError) as error:
        load_acceptance_suite(tmp_path, contract)

    assert error.value.reason == "acceptance_suite_invalid"
    assert "browsers: [chromium, edge]" in error.value.detail


def test_legacy_task_bootstraps_missing_browser_contract_once(tmp_path):
    state = {
        "implementation": {"workspace": {"path": str(tmp_path)}},
        "acceptance": {"status": "passed", "evidence": []},
    }

    bootstrap = asyncio.run(request_browser_acceptance(state))
    assert bootstrap["status"] == RunStatus.RUNNING
    assert bootstrap["blocking_reason"]["code"] == "acceptance_contract_missing"
    assert bootstrap["acceptance_contract_bootstrap_attempted"] is True
    assert route_browser_acceptance({**state, **bootstrap}) == "revision"

    repeated = asyncio.run(request_browser_acceptance(
        {**state, "acceptance_contract_bootstrap_attempted": True}
    ))
    assert repeated["status"] == RunStatus.BLOCKED
    assert repeated["pending_action"]["choices"] == ["revise", "cancel"]
    assert route_browser_acceptance({**state, **repeated}) == "recovery"


def test_browser_contract_dispatches_automatically_when_present(tmp_path):
    contract = tmp_path / ".taskhub" / "acceptance.yaml"
    contract.parent.mkdir()
    contract.write_text("preview: {}", encoding="utf-8")
    state = {
        "implementation": {"workspace": {"path": str(tmp_path)}},
        "acceptance": {"status": "passed", "evidence": []},
    }

    result = asyncio.run(request_browser_acceptance(state))
    assert result == {"status": RunStatus.RUNNING.value}
    assert route_browser_acceptance({**state, **result}) == "execute"


def test_rejected_browser_evidence_preserves_supervision_findings_for_revision():
    state = {
        "acceptance": {"evidence": [{"kind": "browser"}]},
        "supervision": {
            "summary": "真实管理页证据不足",
            "reasons": ["覆盖 390/768/1440 视口", "运行无障碍检查"],
        },
    }

    result = asyncio.run(request_browser_acceptance(state))

    assert result["status"] == RunStatus.BLOCKED
    assert "真实管理页证据不足" in result["blocking_reason"]["detail"]
    assert "覆盖 390/768/1440 视口" in result["blocking_reason"]["detail"]
    assert "运行无障碍检查" in result["blocking_reason"]["detail"]


def test_legacy_browser_block_restores_supervision_findings_when_revising():
    result = asyncio.run(
        prepare_acceptance_revision(
            {
                "blocking_reason": {
                    "code": "browser_evidence_missing",
                    "detail": "Windows 浏览器证据仍未获监督认可",
                },
                "supervision": {
                    "summary": "真实管理页证据不足",
                    "reasons": ["覆盖 390/768/1440 视口", "运行无障碍检查"],
                },
                "revision_count": 1,
                "max_revision_attempts": 2,
            }
        )
    )

    assert "真实管理页证据不足" in result["revision_feedback"]
    assert "覆盖 390/768/1440 视口" in result["revision_feedback"]
    assert "运行无障碍检查" in result["revision_feedback"]


def test_browser_acceptance_uses_unique_job_id_for_retries(monkeypatch, tmp_path):
    import subprocess

    from taskhub_v2.browser.preview import PreviewInstance
    from taskhub_v2.projects import ProjectRegistry
    from taskhub_v2.workers.acceptance import (
        AcceptanceExecutionError,
        ProjectAcceptanceGateway,
    )

    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / ".taskhub").mkdir()
    (repository / ".taskhub" / "acceptance.yaml").write_text(
        """
workload: browser_acceptance
preview:
  command: [python3, tests/e2e/preview.py, --port, "{port}"]
browsers: [chromium]
command: [npx, playwright, test]
suite: tests/e2e/acceptance.yaml
required_artifacts: [junit.xml]
""",
        encoding="utf-8",
    )
    (repository / "tests" / "e2e").mkdir(parents=True)
    (repository / "tests" / "e2e" / "acceptance.yaml").write_text(
        "scenarios:\n  - id: task-list\n    description: Task list\n    browsers: [chromium]\n",
        encoding="utf-8",
    )
    projects_file = tmp_path / "projects.json"
    projects_file.write_text(
        json.dumps({"projects": [{"id": "shop", "repository": str(repository)}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(subprocess, "run", lambda command, **kwargs:
        subprocess.CompletedProcess(command, 0, "a" * 40))

    class Preview:
        async def start(self, run_id, worktree, commit, contract):
            return PreviewInstance(run_id, commit, 8400, "schema", "http://preview", None, "", "")

        async def stop(self, run_id):
            pass

    class Scheduler:
        def __init__(self):
            self.job_ids = []
            self.calls = []

        async def preflight_browser(self, commands, capabilities):
            pass

        async def run(self, job_id, *args, **kwargs):
            self.job_ids.append(job_id)
            self.calls.append(kwargs)
            return ScheduledTests(
                node_id="windows-gui-34",
                tests=[
                    TestExecution(
                        command=["npx", "playwright", "test"],
                        exit_code=0,
                        output_tail="ok",
                    )
                ],
                metadata={
                    "target_url": "http://preview",
                    "git_commit": "a" * 40,
                    "downloaded_artifacts": [{
                        "path": "junit.xml",
                        "sha256": hashlib.sha256(b"<testsuite/>").hexdigest(),
                        "size": len(b"<testsuite/>"),
                        "content": b"<testsuite/>",
                    }],
                },
            )

    scheduler = Scheduler()
    gateway = ProjectAcceptanceGateway(
        ProjectRegistry(str(projects_file)),
        scheduler,
        ArtifactStore(str(tmp_path / "artifacts")),
        Preview(),
    )
    implementation = ExecutionResult(
        summary="done",
        workspace=Workspace(
            project_id="shop",
            path=str(repository),
            branch="task",
            base_commit="a" * 40,
        ),
        commit="a" * 40,
    )

    for _ in range(2):
        with pytest.raises(AcceptanceExecutionError):
            asyncio.run(gateway.verify("run-1", "shop", implementation))

    assert len(set(scheduler.job_ids)) == 2
    assert all(job_id.startswith("run-1-browser-") for job_id in scheduler.job_ids)
    assert all(call["git_commit"] == "a" * 40 for call in scheduler.calls)
    assert all("TASKHUB_GIT_COMMIT" not in call["execution_environment"]
               for call in scheduler.calls)


def test_preproduction_acceptance_deploys_and_binds_real_target(monkeypatch, tmp_path):
    import subprocess

    from taskhub_v2.projects import ProjectRegistry
    from taskhub_v2.workers import acceptance as acceptance_module
    from taskhub_v2.workers.acceptance import ProjectAcceptanceGateway

    repository = tmp_path / "repo"
    (repository / ".taskhub").mkdir(parents=True)
    (repository / "tests" / "e2e").mkdir(parents=True)
    (repository / ".taskhub" / "acceptance.yaml").write_text(
        """
target: preproduction
preproduction:
  prepare_command: [python3, ops/deploy_preproduction.py]
  expected_database_revision: migration-16
browsers: [chromium]
command: [npx, playwright, test]
suite: tests/e2e/acceptance.yaml
required_artifacts: [junit.xml]
""",
        encoding="utf-8",
    )
    (repository / "tests" / "e2e" / "acceptance.yaml").write_text(
        "scenarios:\n  - id: task-list\n    description: Task list\n"
        "    browsers: [chromium]\n",
        encoding="utf-8",
    )
    projects_file = tmp_path / "projects.json"
    projects_file.write_text(json.dumps({"projects": [{
        "id": "shop", "repository": str(repository),
        "acceptance_capabilities": ["test_database"],
        "test_environment": {
            "target_url": "https://preprod.example.com",
            "edge_host": "edge.example.com", "origin_host": "origin.example.com",
        },
    }]}), encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", lambda command, **kwargs:
        subprocess.CompletedProcess(command, 0, "a" * 40))

    async def healthy(environment, specification, commit):
        return {"git_commit": commit, "environment": "production",
                "database_revision": "migration-16"}

    monkeypatch.setattr(acceptance_module, "_wait_for_preproduction", healthy)
    junit = (f'<testsuite><testcase><properties>'
             f'<property name="browser" value="chromium"/>'
             f'<property name="target_url" value="https://preprod.example.com"/>'
             f'<property name="git_commit" value="{"a" * 40}"/>'
             f'<property name="scenario.task-list" value="passed"/>'
             f'</properties></testcase></testsuite>').encode()

    class Scheduler:
        def __init__(self):
            self.calls = []

        async def preflight_browser(self, commands, capabilities):
            pass

        async def run(self, job_id, sticky_key, commands, timeout, workdir, **kwargs):
            self.calls.append((commands, kwargs))
            if kwargs["workload"] == "acceptance":
                return ScheduledTests(node_id="acceptance-54", tests=[TestExecution(
                    command=commands[0], exit_code=0, output_tail="deployed")])
            return ScheduledTests(node_id="windows-gui-34", tests=[TestExecution(
                command=commands[0], exit_code=0, output_tail="passed")], metadata={
                    "target_url": "https://preprod.example.com",
                    "git_commit": "a" * 40,
                    "downloaded_artifacts": [{
                        "path": "junit.xml", "sha256": hashlib.sha256(junit).hexdigest(),
                        "size": len(junit), "content": junit,
                    }],
                })

    class NoPreview:
        async def start(self, *args):
            raise AssertionError("preproduction acceptance must not start a local preview")

    scheduler = Scheduler()
    gateway = ProjectAcceptanceGateway(
        ProjectRegistry(str(projects_file)), scheduler,
        ArtifactStore(str(tmp_path / "artifacts")), NoPreview(),
    )
    result = asyncio.run(gateway.verify("run-1", "shop", ExecutionResult(
        summary="done", workspace=Workspace(
            project_id="shop", path=str(repository), branch="task", base_commit="a" * 40
        ), commit="a" * 40,
    )))

    assert [item.id for item in result.evidence] == [
        "preproduction-deployment", "windows-browser-acceptance"
    ]
    assert scheduler.calls[0][0] == [["python3", "ops/deploy_preproduction.py"]]
    assert scheduler.calls[0][1]["git_commit"] == "a" * 40
    assert "TASKHUB_GIT_COMMIT" not in scheduler.calls[0][1]["execution_environment"]
    assert scheduler.calls[1][1]["target_url"] == "https://preprod.example.com"
    assert scheduler.calls[1][1]["execution_environment"]["TASKHUB_TEST_EDGE_HOST"] == (
        "edge.example.com"
    )


def test_browser_acceptance_surfaces_collection_failure(monkeypatch, tmp_path):
    import subprocess

    from taskhub_v2.browser.preview import PreviewInstance
    from taskhub_v2.projects import ProjectRegistry
    from taskhub_v2.workers.acceptance import AcceptanceExecutionError, ProjectAcceptanceGateway

    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / ".taskhub").mkdir()
    (repository / ".taskhub" / "acceptance.yaml").write_text(
        """
workload: browser_acceptance
preview:
  command: [python3, tests/e2e/preview.py, --port, "{port}"]
browsers: [chromium]
command: [npx, playwright, test]
suite: tests/e2e/acceptance.yaml
required_artifacts: [junit.xml]
""",
        encoding="utf-8",
    )
    (repository / "tests" / "e2e").mkdir(parents=True)
    (repository / "tests" / "e2e" / "acceptance.yaml").write_text(
        "scenarios:\n  - id: task-list\n    description: Task list\n    browsers: [chromium]\n",
        encoding="utf-8",
    )
    projects_file = tmp_path / "projects.json"
    projects_file.write_text(
        json.dumps({"projects": [{"id": "shop", "repository": str(repository)}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(subprocess, "run", lambda command, **kwargs:
        subprocess.CompletedProcess(command, 0, "a" * 40))

    class Preview:
        async def start(self, run_id, worktree, commit, contract):
            return PreviewInstance(run_id, commit, 8400, "schema", "http://preview", None, "", "")

        async def stop(self, run_id):
            pass

    class Scheduler:
        async def preflight_browser(self, commands, capabilities):
            pass

        async def run(self, *args, **kwargs):
            empty_junit = b'<testsuites tests="0"></testsuites>'
            return ScheduledTests(
                node_id="windows-gui-34",
                tests=[TestExecution(
                    command=["npx", "playwright", "test"],
                    exit_code=1,
                    output_tail="Error: spawnSync git ENOENT",
                )],
                metadata={
                    "target_url": "http://preview",
                    "git_commit": "a" * 40,
                    "downloaded_artifacts": [{
                        "path": "junit.xml",
                        "sha256": hashlib.sha256(empty_junit).hexdigest(),
                        "size": len(empty_junit),
                        "content": empty_junit,
                    }],
                },
            )

    gateway = ProjectAcceptanceGateway(
        ProjectRegistry(str(projects_file)),
        Scheduler(),
        ArtifactStore(str(tmp_path / "artifacts")),
        Preview(),
    )
    implementation = ExecutionResult(
        summary="done",
        workspace=Workspace(
            project_id="shop",
            path=str(repository),
            branch="task",
            base_commit="a" * 40,
        ),
        commit="a" * 40,
    )

    with pytest.raises(AcceptanceExecutionError, match="spawnSync git ENOENT"):
        asyncio.run(gateway.verify("run-1", "shop", implementation))


def test_browser_acceptance_allows_workspace_descendant_commit(monkeypatch, tmp_path):
    import subprocess

    from taskhub_v2.workers.acceptance import _browser_acceptance_commit

    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[3] == "rev-parse":
            return subprocess.CompletedProcess(command, 0, "b" * 40)
        if command[3] == "merge-base":
            return subprocess.CompletedProcess(command, 0, "")
        raise AssertionError(command)

    monkeypatch.setattr(subprocess, "run", run)

    assert _browser_acceptance_commit(str(tmp_path), "a" * 40) == "b" * 40
    assert calls[-1][-2:] == ["a" * 40, "b" * 40]


def test_browser_acceptance_rejects_unrelated_workspace_commit(monkeypatch, tmp_path):
    import subprocess

    from taskhub_v2.workers.acceptance import (
        AcceptanceExecutionError,
        _browser_acceptance_commit,
    )

    def run(command, **kwargs):
        if command[3] == "rev-parse":
            return subprocess.CompletedProcess(command, 0, "b" * 40)
        if command[3] == "merge-base":
            return subprocess.CompletedProcess(command, 1, "")
        raise AssertionError(command)

    monkeypatch.setattr(subprocess, "run", run)

    with pytest.raises(AcceptanceExecutionError, match="commit does not match"):
        _browser_acceptance_commit(str(tmp_path), "a" * 40)


def test_project_e2e_suite_definition_is_loaded_and_complete():
    repository = Path(__file__).parents[1]
    contract = load_acceptance_contract(repository)
    suite = load_acceptance_suite(repository, contract)
    assert {scenario.id for scenario in suite.scenarios} == {
            "login_status", "structured_blocking", "refresh_consistency",
            "dual_context_consistency", "retry_recovery", "action_visibility",
            "artifact_view", "resource_center",
        }
    assert all(scenario.browsers == {"chromium", "edge"} for scenario in suite.scenarios)


def test_artifact_store_verifies_agent_digest_and_size(tmp_path):
    store = ArtifactStore(str(tmp_path))
    payload = b"trace"
    artifact = store.write_bytes(
        "run-1", "trace.zip", "browser-trace", payload,
        expected_sha256=hashlib.sha256(payload).hexdigest(),
    )
    assert artifact.metadata["verified"] is True
    with pytest.raises(ValueError, match="digest mismatch"):
        store.write_bytes("run-1", "bad.zip", "trace", payload, expected_sha256="0" * 64)
    with pytest.raises(ValueError, match="size limit"):
        store.write_bytes("run-1", "large.zip", "trace", payload, max_bytes=2)


def test_preview_cancellation_cleans_schema_process_and_state(monkeypatch, tmp_path):
    import asyncio
    import subprocess

    from taskhub_v2.browser.contract import PreviewContract
    from taskhub_v2.browser.preview import PreviewManager

    async def in_thread(function, *args, **kwargs):
        return function(*args, **kwargs)
    monkeypatch.setattr(asyncio, "to_thread", in_thread)
    calls = []
    manager = PreviewManager(
        "postgresql://unused",
        host="127.0.0.1",
        ports=[8499],
        public_url="http://seed.example:8200",
    )
    monkeypatch.setattr(manager, "_create_schema", lambda schema: calls.append(("create", schema)))
    monkeypatch.setattr(manager, "_drop_schema", lambda schema: calls.append(("drop", schema)))
    monkeypatch.setattr(subprocess, "run", lambda command, **kwargs:
        subprocess.CompletedProcess(command, 0, "a" * 40 if command[-1] == "HEAD" else ""))

    async def exercise():
        ready = asyncio.Event()
        health_urls = []
        async def wait(*args, **kwargs):
            health_urls.append(args[0])
            ready.set()
            await asyncio.Event().wait()
        monkeypatch.setattr(manager, "_wait_ready", wait)
        task = asyncio.create_task(manager.start("run", str(tmp_path), "a" * 40,
            PreviewContract(command=[
                sys.executable, "-c", "import time; time.sleep(60); port='{port}'"
            ])))
        done, _ = await asyncio.wait(
            [task, asyncio.create_task(ready.wait())],
            timeout=5,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if task in done:
            await task
        assert ready.is_set()
        instance = manager._instances["run"]
        assert instance.url == "http://seed.example:8499"
        assert health_urls == ["http://127.0.0.1:8499/api/health"]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert instance.process.returncode is not None
        assert not Path(instance.state_dir).exists()
        assert not manager._instances
    asyncio.run(exercise())
    assert calls[0][0] == "create" and calls[1] == ("drop", calls[0][1])


def test_preview_uses_candidate_source_instead_of_controller_pythonpath(
    monkeypatch, tmp_path
):
    import subprocess

    from taskhub_v2.browser.contract import PreviewContract
    from taskhub_v2.browser.preview import PreviewManager

    (tmp_path / "src").mkdir()
    manager = PreviewManager("postgresql://unused", host="127.0.0.1", ports=[8498])
    monkeypatch.setattr(manager, "_create_schema", lambda schema: None)
    monkeypatch.setattr(manager, "_drop_schema", lambda schema: None)
    monkeypatch.setattr(subprocess, "run", lambda command, **kwargs:
        subprocess.CompletedProcess(command, 0, "a" * 40 if command[-1] == "HEAD" else ""))
    captured = {}

    async def spawn(*command, **kwargs):
        captured.update(kwargs["env"])
        raise RuntimeError("captured")

    monkeypatch.setenv("PYTHONPATH", "/deployed/controller/src")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(RuntimeError, match="captured"):
        asyncio.run(manager.start(
            "run", str(tmp_path), "a" * 40,
            PreviewContract(command=[sys.executable, "-c", "port='{port}'"]),
        ))

    assert captured["PYTHONPATH"] == str(tmp_path / "src")


def test_preview_manager_uses_configured_reachable_host(tmp_path):
    from taskhub_v2.browser.preview import PreviewManager

    manager = PreviewManager("postgresql://unused", host="192.168.31.51", ports=[8498])

    assert manager.host == "192.168.31.51"


def test_preview_manager_reuses_seed_public_host_when_default_is_loopback():
    from taskhub_v2.browser.preview import PreviewManager

    manager = PreviewManager(
        "postgresql://unused",
        host="127.0.0.1",
        public_url="http://192.168.31.31:8200",
    )

    assert manager.host == "192.168.31.31"


def test_preview_manager_keeps_explicit_routable_host():
    from taskhub_v2.browser.preview import PreviewManager

    manager = PreviewManager(
        "postgresql://unused",
        host="preview.internal",
        public_url="http://seed.internal:8200",
    )

    assert manager.host == "preview.internal"


def test_preview_host_is_loaded_from_environment(monkeypatch):
    from taskhub_v2.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("TASKHUB_PREVIEW_HOST", "192.168.31.51")
    try:
        assert get_settings().preview_host == "192.168.31.51"
    finally:
        get_settings.cache_clear()


def test_preview_rejects_healthy_old_version(monkeypatch):
    import httpx

    from taskhub_v2.browser.preview import PreviewManager

    client_class = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, json={"status": "ok", "git_commit": "b" * 40}
    ))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs:
                        client_class(transport=transport, **kwargs))
    manager = PreviewManager("postgresql://unused")
    with pytest.raises(ValueError, match="health commit mismatch"):
        asyncio.run(manager._wait_ready("http://candidate:8400/api/health", 1, "a" * 40))


def test_junit_browser_count_cannot_substitute_for_scenario_coverage():
    matrix = {"refresh_consistency": {"chromium", "edge"},
              "dual_context_consistency": {"chromium", "edge"}}

    def report(browser, scenarios):
        properties = ''.join(f'<property name="scenario.{name}" value="passed"/>'
                             for name in scenarios)
        return (f'<testsuite><testcase><properties><property name="browser" '
                f'value="{browser}"/>{properties}</properties></testcase></testsuite>').encode()

    incomplete = [report(browser, ["refresh_consistency"]) for browser in ("chromium", "edge")]
    with pytest.raises(ValueError, match="dual_context_consistency/edge"):
        validate_junit(incomplete, ["chromium", "edge"], scenarios=matrix)
    complete = [report(browser, matrix) for browser in ("chromium", "edge")]
    validate_junit(complete, ["chromium", "edge"], scenarios=matrix)
    with pytest.raises(ValueError, match="scenario coverage"):
        validate_junit([complete[0], incomplete[1]], ["chromium", "edge"], scenarios=matrix)
