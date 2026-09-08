import hashlib
import io
import sys
import tarfile

import httpx
from fastapi.testclient import TestClient

from taskhub_v2.domain.models import NodeDefinition, Plan
from taskhub_v2.execution.runner import NodeRunner
from taskhub_v2.node_agent import create_node_app


def archive(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as bundle:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            bundle.addfile(info, io.BytesIO(content))
    return output.getvalue()


def test_node_uploads_workspace_and_executes_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("TASKHUB_NODE_ID", "node-test")
    monkeypatch.setenv("TASKHUB_NODE_TOKEN", "node-secret")
    monkeypatch.setenv("TASKHUB_NODE_WORK_ROOT", str(tmp_path / "jobs"))
    payload = archive({"value.txt": b"ready\n"})
    digest = hashlib.sha256(payload).hexdigest()
    headers = {"Authorization": "Bearer node-secret"}

    with TestClient(create_node_app()) as client:
        assert client.get("/api/health").status_code == 401
        health = client.get("/api/health", headers=headers)
        uploaded = client.put(
            f"/api/jobs/job-1/workspace?sha256={digest}", content=payload, headers=headers
        )
        assert uploaded.status_code == 200
        response = client.post(
            "/api/jobs/job-1/execute",
            json={
                "commands": [
                    [
                        "python3",
                        "-c",
                        "from pathlib import Path; "
                        "assert Path('value.txt').read_text() == 'ready\\n'; "
                        "import os; "
                        "assert os.environ['TASKHUB_CHROMIUM_CHANNEL'] == 'chrome'; "
                        "assert os.environ['TASKHUB_EDGE_CHANNEL'] == 'msedge'; "
                        "assert os.environ['PYTEST_ADDOPTS'] == '-ra'",
                    ]
                ],
                "timeout_seconds": 30,
                "archive_sha256": digest,
            },
            headers=headers,
        )
    assert response.status_code == 200
    assert response.json()["node_id"] == "node-test"
    assert {"cpu_percent", "memory_used_percent", "disk_used_percent"} <= set(
        health.json()["load"]
    )
    assert response.json()["tests"][0]["exit_code"] == 0


def test_node_reuses_persisted_execution_result(tmp_path, monkeypatch):
    import taskhub_v2.node_agent.app as agent_module

    monkeypatch.setenv("TASKHUB_NODE_ID", "node-test")
    monkeypatch.setenv("TASKHUB_NODE_TOKEN", "node-secret")
    monkeypatch.setenv("TASKHUB_NODE_WORK_ROOT", str(tmp_path / "jobs"))
    payload = archive({"value.txt": b"ready\n"})
    digest = hashlib.sha256(payload).hexdigest()
    headers = {"Authorization": "Bearer node-secret"}
    calls = 0

    async def fake_run_commands(*args, **kwargs):
        nonlocal calls
        calls += 1
        return [{"command": ["test"], "exit_code": 0, "output_tail": "ok"}]

    monkeypatch.setattr(agent_module, "run_commands", fake_run_commands)
    request = {"commands": [["test"]], "timeout_seconds": 30,
               "archive_sha256": digest}
    with TestClient(create_node_app()) as client:
        client.put(f"/api/jobs/job-reuse/workspace?sha256={digest}",
                   content=payload, headers=headers).raise_for_status()
        first = client.post("/api/jobs/job-reuse/execute", json=request, headers=headers)
        uploaded = client.put(f"/api/jobs/job-reuse/workspace?sha256={digest}",
                              content=payload, headers=headers)
        second = client.post("/api/jobs/job-reuse/execute", json=request, headers=headers)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert uploaded.json()["reused"] is True
    assert calls == 1


def test_node_repairs_incomplete_managed_virtualenv_before_execute(
    tmp_path, monkeypatch
):
    import taskhub_v2.node_agent.app as agent_module

    monkeypatch.setenv("TASKHUB_NODE_ID", "node-test")
    monkeypatch.setenv("TASKHUB_NODE_TOKEN", "node-secret")
    monkeypatch.setenv("TASKHUB_NODE_WORK_ROOT", str(tmp_path / "jobs"))
    payload = archive({"value.txt": b"ready\n"})
    digest = hashlib.sha256(payload).hexdigest()
    headers = {"Authorization": "Bearer node-secret"}
    observed = []

    async def fake_run_commands(workdir, *args, **kwargs):
        observed.append(not (workdir / ".venv").exists())
        return [{"command": ["test"], "exit_code": 0, "output_tail": "ok"}]

    monkeypatch.setattr(agent_module, "run_commands", fake_run_commands)
    request = {
        "commands": [["test"]], "timeout_seconds": 30, "archive_sha256": digest
    }
    with TestClient(create_node_app()) as client:
        client.put(
            f"/api/jobs/job-repair/workspace?sha256={digest}",
            content=payload,
            headers=headers,
        ).raise_for_status()
        job = tmp_path / "jobs" / "job-repair"
        (job / ".venv" / "bin").mkdir(parents=True)
        stale = job / ".taskhub-execution-result.json"
        stale.write_text('{"request_key":"stale","result":{}}', encoding="utf-8")
        response = client.post(
            "/api/jobs/job-repair/execute", json=request, headers=headers
        )

    assert response.status_code == 200
    assert observed == [True]
    assert response.json()["tests"][0]["exit_code"] == 0


def test_browser_node_installs_npm_dependencies_before_npx(tmp_path, monkeypatch):
    import taskhub_v2.node_agent.app as agent_module

    monkeypatch.setenv("TASKHUB_NODE_ID", "node-test")
    monkeypatch.setenv("TASKHUB_NODE_TOKEN", "node-secret")
    monkeypatch.setenv("TASKHUB_NODE_WORK_ROOT", str(tmp_path / "jobs"))
    monkeypatch.setattr(agent_module, "detect_capabilities", lambda: {"playwright": True})
    payload = archive({"package.json": b'{"devDependencies":{"@playwright/test":"1.58.2"}}'})
    digest = hashlib.sha256(payload).hexdigest()
    headers = {"Authorization": "Bearer node-secret"}
    installed = []
    real_run = agent_module.subprocess.run

    def fake_install(command, **kwargs):
        if "cwd" not in kwargs:
            return real_run(command, **kwargs)
        installed.append((command, kwargs["cwd"]))
        (kwargs["cwd"] / "node_modules").mkdir()
        return agent_module.subprocess.CompletedProcess(command, 0, "installed\n", "")

    async def fake_run_commands(*args, **kwargs):
        return [{"command": ["npx", "playwright", "test"], "exit_code": 0, "output_tail": "ok"}]

    monkeypatch.setattr(agent_module.subprocess, "run", fake_install)
    monkeypatch.setattr(agent_module, "run_commands", fake_run_commands)

    with TestClient(create_node_app()) as client:
        uploaded = client.put(
            f"/api/jobs/job-browser/workspace?sha256={digest}",
            content=payload,
            headers=headers,
        )
        assert uploaded.status_code == 200
        response = client.post(
            "/api/jobs/job-browser/execute",
            json={
                "commands": [["npx", "playwright", "test"]],
                "timeout_seconds": 30,
                "archive_sha256": digest,
                "required_capabilities": ["playwright"],
            },
            headers=headers,
        )

    assert response.status_code == 200
    assert installed[0][0][:2] == ["npm", "install"]
    tests = response.json()["tests"]
    assert tests[0]["command"][:2] == ["npm", "install"]
    assert tests[1]["command"] == ["npx", "playwright", "test"]


def test_local_runner_uses_its_own_python_environment(tmp_path):
    import asyncio

    expected = sys.executable
    result = asyncio.run(NodeRunner._run_local(
        [["python3", "-c", f"import sys; assert sys.executable == {expected!r}"]],
        30,
        str(tmp_path),
    ))

    assert result[0].command[0] == expected
    assert result[0].exit_code == 0


def test_node_rejects_archive_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setenv("TASKHUB_NODE_TOKEN", "node-secret")
    monkeypatch.setenv("TASKHUB_NODE_WORK_ROOT", str(tmp_path / "jobs"))
    payload = archive({"../escape.txt": b"no"})
    digest = hashlib.sha256(payload).hexdigest()

    with TestClient(create_node_app()) as client:
        response = client.put(
            f"/api/jobs/job-1/workspace?sha256={digest}",
            content=payload,
            headers={"Authorization": "Bearer node-secret"},
        )
    assert response.status_code == 422
    assert not (tmp_path / "escape.txt").exists()


def test_node_rejects_environment_file_variants(tmp_path, monkeypatch):
    monkeypatch.setenv("TASKHUB_NODE_TOKEN", "node-secret")
    monkeypatch.setenv("TASKHUB_NODE_WORK_ROOT", str(tmp_path / "jobs"))
    payload = archive({".env.production": b"SECRET=value"})
    digest = hashlib.sha256(payload).hexdigest()

    with TestClient(create_node_app()) as client:
        response = client.put(
            f"/api/jobs/job-env/workspace?sha256={digest}",
            content=payload,
            headers={"Authorization": "Bearer node-secret"},
        )
    assert response.status_code == 422


def test_remote_runner_uploads_and_executes_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("TASKHUB_NODE_ID", "remote-test")
    monkeypatch.setenv("TASKHUB_NODE_TOKEN", "node-secret")
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    monkeypatch.setenv("TASKHUB_NODE_WORK_ROOT", str(jobs))
    app = create_node_app()
    transport = httpx.ASGITransport(app=app)
    runner = NodeRunner("node-secret", transport=transport)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / "value.txt").write_text("ready\n", encoding="utf-8")

    import asyncio

    result = asyncio.run(
        runner.run(
            NodeDefinition(id="remote-test", kind="remote", url="http://node"),
            "job-remote",
            [["python3", "-c", "from pathlib import Path; assert Path('value.txt').is_file()"]],
            30,
            str(worktree),
        )
    )
    assert result.node_id == "remote-test"
    assert result.tests[0].exit_code == 0


def test_remote_runner_applies_coding_result(tmp_path, monkeypatch):
    import json

    import taskhub_v2.node_agent.app as agent_module

    monkeypatch.setenv("TASKHUB_NODE_ID", "remote-coder")
    monkeypatch.setenv("TASKHUB_NODE_TOKEN", "node-secret")
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    monkeypatch.setenv("TASKHUB_NODE_WORK_ROOT", str(jobs))
    monkeypatch.setattr(agent_module, "coding_available", lambda: True)

    async def fake_modify(workdir, requirement, plan, feedback):
        (workdir / "value.txt").write_text("changed\n", encoding="utf-8")
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as bundle:
            manifest = json.dumps(
                {
                    "summary": "changed value",
                    "tests": [],
                    "provider": "test-provider",
                    "model": "test-model",
                    "duration_ms": 1,
                    "failed_providers": [],
                    "changed_files": ["value.txt"],
                    "deleted_files": [],
                }
            ).encode()
            info = tarfile.TarInfo("manifest.json")
            info.size = len(manifest)
            bundle.addfile(info, io.BytesIO(manifest))
            bundle.add(workdir / "value.txt", arcname="files/value.txt")
        return output.getvalue()

    monkeypatch.setattr(agent_module, "modify_workspace", fake_modify)
    transport = httpx.ASGITransport(app=create_node_app())
    runner = NodeRunner("node-secret", transport=transport)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / "value.txt").write_text("before\n", encoding="utf-8")

    import asyncio

    result = asyncio.run(
        runner.run_coding(
            NodeDefinition(id="remote-coder", kind="remote", url="http://node"),
            "job-code",
            "change value",
            Plan(summary="change", steps=["edit"], acceptance=["changed"]),
            "",
            30,
            str(worktree),
        )
    )
    assert result.node_id == "remote-coder"
    assert result.result.content.summary == "changed value"
    assert (worktree / "value.txt").read_text() == "changed\n"


def test_node_health_disables_coding_when_workspace_sandbox_fails(tmp_path, monkeypatch):
    import taskhub_v2.node_agent.app as agent_module

    monkeypatch.setenv("TASKHUB_NODE_ID", "node-test")
    monkeypatch.setenv("TASKHUB_NODE_TOKEN", "node-secret")
    monkeypatch.setenv("TASKHUB_NODE_WORK_ROOT", str(tmp_path / "jobs"))
    monkeypatch.setattr(agent_module, "coding_available", lambda: True)
    monkeypatch.setattr(agent_module, "coding_prerequisites_ok", lambda: False)
    monkeypatch.setattr(agent_module, "node_diagnostics", lambda: {
        "role": "node",
        "host": "node-test",
        "platform": {},
        "checks": [{
            "category": "preflight",
            "name": "User namespace",
            "status": "fail",
            "detail": "Operation not permitted",
            "expected": "",
            "actual": "",
            "recommendation": "fix sandbox",
        }],
    })

    with TestClient(create_node_app()) as client:
        response = client.get("/api/health", headers={"Authorization": "Bearer node-secret"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["capabilities"]["workspace_write_sandbox"] is False
    assert payload["capabilities"]["coding"] is False
    assert payload["system"]["checks"][0]["status"] == "fail"
