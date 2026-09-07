import hashlib
import asyncio
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
from taskhub_v2.domain.models import RunStatus
from taskhub_v2.workflows.browser_acceptance import (
    request_browser_acceptance,
    route_browser_acceptance,
)
from taskhub_v2.node_agent.runtime import normalize_command
from taskhub_v2.node_agent.runtime import run_commands
from taskhub_v2.browser.reports import validate_junit


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
    report = b"<testsuite failures='1'><testcase><failure>UnicodeDecodeError: gbk</failure></testcase></testsuite>"
    with pytest.raises(ValueError, match="UnicodeDecodeError: gbk"):
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
    from taskhub_v2.browser.preview import PreviewManager
    from taskhub_v2.browser.contract import PreviewContract

    async def in_thread(function, *args, **kwargs):
        return function(*args, **kwargs)
    monkeypatch.setattr(asyncio, "to_thread", in_thread)
    calls = []
    manager = PreviewManager("postgresql://unused", host="127.0.0.1", ports=[8499])
    monkeypatch.setattr(manager, "_create_schema", lambda schema: calls.append(("create", schema)))
    monkeypatch.setattr(manager, "_drop_schema", lambda schema: calls.append(("drop", schema)))
    monkeypatch.setattr(subprocess, "run", lambda command, **kwargs:
        subprocess.CompletedProcess(command, 0, "a" * 40 if command[-1] == "HEAD" else ""))

    async def exercise():
        ready = asyncio.Event()
        async def wait(*args, **kwargs):
            ready.set()
            await asyncio.Event().wait()
        monkeypatch.setattr(manager, "_wait_ready", wait)
        task = asyncio.create_task(manager.start("run", str(tmp_path), "a" * 40,
            PreviewContract(command=[
                sys.executable, "-c", "import time; time.sleep(60); port='{port}'"
            ])))
        done, _ = await asyncio.wait([task, asyncio.create_task(ready.wait())], timeout=5, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            await task
        assert ready.is_set()
        instance = manager._instances["run"]
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
    from taskhub_v2.browser.preview import PreviewManager
    from taskhub_v2.browser.contract import PreviewContract

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
