"""Exercise lifecycle guards through HTTP with real graphs and checkpoints."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import InMemorySaver

from taskhub_v2.api.app import create_app
from taskhub_v2.config import Settings
from taskhub_v2.persistence.task_index import MemoryTaskIndex
from taskhub_v2.projects import ProjectRegistry
from taskhub_v2.services.runs import RunService
from taskhub_v2.workflows import build_main_graph
from tests.fakes import RecordingProvider
from tests.test_workflow import RecoveringPublisher, RecoveringWorker

pytestmark = pytest.mark.anyio
EVIDENCE = {"evidence": [
    {"id": "test", "status": "passed", "source": "test", "summary": "passed"}
]}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def lifecycle(tmp_path, monkeypatch):
    registry_path = tmp_path / "projects.json"

    def projects(*ids):
        registry_path.write_text(json.dumps({"projects": [
            {"id": project_id, "repository": str(tmp_path / project_id)} for project_id in ids
        ]}))

    projects("original", "replacement")
    settings = Settings(
        checkpointer="memory", projects_file=str(registry_path),
        admin_token="admin-secret", session_secret="session-secret",
        provider_health_file=str(tmp_path / "health.json"),
        nodes_file=str(tmp_path / "nodes.json"),
        node_state_file=str(tmp_path / "node-state.json"),
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app), AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        provider, worker, publisher = RecordingProvider(), RecoveringWorker(), RecoveringPublisher()
        graph = build_main_graph(provider, worker, InMemorySaver(), publisher)
        stream = Mock(wraps=graph.astream)
        monkeypatch.setattr(graph, "astream", stream)
        execute = Mock(wraps=worker.execute)
        monkeypatch.setattr(worker, "execute", execute)
        index = MemoryTaskIndex()
        app.state.run_service = RunService(graph, ProjectRegistry(str(registry_path)), index)
        assert (await client.post(
            "/api/auth/login", json={"token": "admin-secret"}
        )).status_code == 200
        headers = {"X-CSRF-Token": client.cookies.get("taskhub_v2_csrf")}
        response = await client.post("/api/runs", headers=headers, json={
            "project_id": "original", "requirement": "Recover lifecycle task",
        })
        assert response.status_code == 201
        base = "/api/runs/" + response.json()["run_id"]
        yield SimpleNamespace(
            client=client, headers=headers, base=base, projects=projects,
            stream=stream, worker=worker, execute=execute, provider=provider, publisher=publisher,
        )


async def post(task, route, payload=None):
    return await task.client.post(task.base + route, headers=task.headers, json=payload)


async def reach(task, stage):
    if stage == "implementation_blocked":
        return
    assert (await post(task, "/resume", {"decision": "retry"})).json()["stage"] == "merge_blocked"


@pytest.mark.parametrize("stage", ["implementation_blocked", "merge_blocked"])
async def test_orphan_retry_submits_no_command_and_rebind_recovers(lifecycle, stage):
    task = lifecycle
    await reach(task, stage)
    task.projects("replacement")
    detail = (await task.client.get(task.base)).json()
    listed = (await task.client.get("/api/runs")).json()["items"][0]
    for item in (detail, listed):
        assert item["orphaned"] and item["project_missing"]
        assert item["allowed_actions"] == ["archive", "rebind_project"]
    history = (await task.client.get(task.base + "/history")).json()
    calls = (task.worker.calls, task.publisher.calls)
    task.stream.reset_mock()
    response = await post(task, "/resume", {"decision": "retry"})
    assert response.status_code == 409
    assert "archive" in response.json()["detail"] and "rebind" in response.json()["detail"]
    task.stream.assert_not_called()
    assert (task.worker.calls, task.publisher.calls) == calls
    assert (await task.client.get(task.base + "/history")).json() == history

    assert (await post(task, "/rebind", {"project_id": "missing"})).status_code == 409
    assert (await task.client.get(task.base)).json()["project_id"] == "original"
    rebound = await post(task, "/rebind", {"project_id": "replacement"})
    assert rebound.status_code == 200
    assert rebound.json()["project_id"] == "replacement"
    assert rebound.json()["original_project_id"] == "original"
    assert not rebound.json()["project_missing"]
    assert (await task.client.get(task.base + "/history")).json() == history
    task.stream.assert_not_called()

    response = await post(task, "/resume", {"decision": "retry"})
    assert response.status_code == 200
    task.stream.assert_called_once()
    if stage == "implementation_blocked":
        assert response.json()["stage"] == "merge_blocked"
        assert task.execute.call_args.args[1] == "replacement"
        assert task.worker.calls == 2
        response = await post(task, "/resume", {"decision": "retry"})
    assert response.json()["status"] == "completed"
    assert response.json()["publication"]["project_id"] == "replacement"
    assert task.provider.plan_calls == task.provider.review_calls == 1
    assert task.provider.supervisor_calls == 1


@pytest.mark.parametrize("stage,route,payload", [
    ("implementation_blocked", "/resume", {"decision": "retry"}),
    ("implementation_blocked", "/resume", {"decision": "cancel"}),
    ("merge_blocked", "/resume", {"decision": "retry"}),
    ("implementation_blocked", "/acceptance", EVIDENCE),
    ("implementation_blocked", "/rebind", {"project_id": "replacement"}),
])
async def test_archived_actions_are_rejected_before_graph_execution(
    lifecycle, stage, route, payload
):
    task = lifecycle
    await reach(task, stage)
    task.projects("replacement")
    archived = await post(task, "/archive")
    assert archived.status_code == 200 and archived.json()["archived_at"]
    assert (await task.client.get("/api/runs")).json()["total"] == 0
    page = (await task.client.get("/api/runs?include_archived=true")).json()
    assert page["total"] == 1
    assert page["items"][0]["archived_at"] == archived.json()["archived_at"]
    assert (await post(task, "/archive")).json()["archived_at"] == archived.json()["archived_at"]
    history = (await task.client.get(task.base + "/history")).json()
    task.stream.reset_mock()
    response = await post(task, route, payload)
    assert response.status_code == 409
    assert "archived" in response.json()["detail"]
    task.stream.assert_not_called()
    assert (await task.client.get(task.base + "/history")).json() == history


@pytest.mark.parametrize("stage,route,payload", [
    ("implementation_blocked", "/resume", {"decision": "retry"}),
    ("implementation_blocked", "/acceptance", EVIDENCE),
    ("merge_blocked", "/resume", {"decision": "retry"}),
])
async def test_orphan_other_execution_entries_are_guarded(lifecycle, stage, route, payload):
    task = lifecycle
    await reach(task, stage)
    task.projects("replacement")
    task.stream.reset_mock()
    response = await post(task, route, payload)
    assert response.status_code == 409
    assert "archive" in response.json()["detail"] and "rebind" in response.json()["detail"]
    task.stream.assert_not_called()
