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
                        "assert os.environ['TASKHUB_EDGE_CHANNEL'] == 'msedge'",
                    ]
                ],
                "timeout_seconds": 30,
                "archive_sha256": digest,
            },
            headers=headers,
        )
    assert response.status_code == 200
    assert response.json()["node_id"] == "node-test"
    assert response.json()["tests"][0]["exit_code"] == 0


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
