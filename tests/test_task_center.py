import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from taskhub_v2.api.app import create_app
from taskhub_v2.config import Settings
from taskhub_v2.domain.models import StartRunRequest
from taskhub_v2.persistence.task_index import MemoryTaskIndex
from taskhub_v2.services.runs import RunService
from taskhub_v2.services.task_state import checkpoint_values, workflow_steps
from taskhub_v2.workflows import build_main_graph
from tests.fakes import RecordingProvider, RecordingWorker
from tests.test_workflow import RecoveringPublisher


def settings():
    return Settings(
        checkpointer="memory", admin_token="admin-secret", session_secret="session-secret"
    )


def login(client):
    client.post("/api/auth/login", json={"token": "admin-secret"})
    return {"X-CSRF-Token": client.cookies.get("taskhub_v2_csrf")}


def test_memory_task_index_filters_and_preserves_created_time():
    async def exercise():
        index = MemoryTaskIndex()
        first = await index.upsert({
            "run_id": "one", "requirement": "First task", "project_id": "demo",
            "current_stage": "planning", "status": "running",
        }, "A")
        await index.upsert({
            "run_id": "one", "requirement": "First task", "project_id": "demo",
            "current_stage": "plan_approval", "status": "waiting",
            "pending_action": {"type": "plan_approval", "choices": ["approve", "reject"]},
        })
        page = await index.list(production_line="A", status="waiting")
        assert page.total == 1
        assert page.items[0].created_at == first.created_at
        assert page.items[0].updated_at >= first.updated_at
    asyncio.run(exercise())


def test_task_center_lists_three_tasks_and_filters_production_lines():
    with TestClient(create_app(settings())) as client:
        headers = login(client)
        for number, line in enumerate(("A", "B", "A"), 1):
            response = client.post("/api/runs", headers=headers, json={
                "project_id": "demo", "requirement": f"Build feature {number}",
                "production_line": line,
            })
            assert response.status_code == 201
        page = client.get("/api/runs").json()
        assert page["total"] == 3
        filtered = client.get("/api/runs?production_line=A&status=waiting").json()
        assert filtered["total"] == 2
        assert {item["production_line"] for item in filtered["items"]} == {"A"}
        assert all(item["pending_action"]["choices"] for item in filtered["items"])


def test_task_detail_exposes_backend_action_and_eleven_stage_ui():
    with TestClient(create_app(settings())) as client:
        headers = login(client)
        run = client.post("/api/runs", headers=headers, json={
            "project_id": "demo", "requirement": "Build task details"
        }).json()
        detail = client.get(f"/api/runs/{run['run_id']}").json()
        assert detail["pending_action"]["choices"] == ["approve", "reject"]
        assert len(detail["workflow_steps"]) == 11
        assert detail["workflow_steps"][2]["state"] == "waiting_manual"
        assert detail["created_at"] and detail["updated_at"]
        html = client.get("/").text
        assert html.count("任务中心") >= 1
        assert 'id="nav-workflow"' in html and "开发流程" in html
        assert 'id="nav-resources"' in html and "系统资源" in html
        assert '<details><summary>规划方案' in html
        script = client.get("/static/app.js").text
        assert "localStorage.setItem(\"taskhub_run_id\"" not in script
        for stage in (
            "intake", "planning", "plan_approval", "implementation", "acceptance",
            "review", "risk", "supervision", "merge_approval", "merging", "completed",
        ):
            assert f'"{stage}"' in script


def test_task_center_approvals_and_recovery_do_not_repeat_completed_work():
    app = create_app(settings())
    with TestClient(app) as client:
        provider, worker, publisher = RecordingProvider(), RecordingWorker(), RecoveringPublisher()
        app.state.run_service = RunService(
            build_main_graph(provider, worker, InMemorySaver(), publisher),
            task_index=MemoryTaskIndex(),
        )
        headers = login(client)
        run = client.post('/api/runs', headers=headers, json={
            'project_id': 'demo', 'requirement': 'Publish with recovery', 'production_line': 'A'
        }).json()
        run_id = client.get('/api/runs').json()['items'][0]['run_id']
        assert run_id == run['run_id']
        base = f'/api/runs/{run_id}'
        assert client.get(base).json()['workflow_steps'][2]['state'] == 'waiting_manual'
        approved = client.post(base + '/approval', headers=headers,
                               json={'decision': 'approve'}).json()
        assert approved['workflow_steps'][8]['state'] == 'waiting_manual'
        blocked = client.post(base + '/resume', headers=headers,
                              json={'decision': 'approve'}).json()
        assert blocked['blocking_reason']['detail'] == 'temporary publication failure'
        assert blocked['pending_action']['choices'] == ['retry', 'cancel']
        assert blocked['workflow_steps'][9]['state'] == 'blocked'
        assert client.get('/api/runs?status=blocked').json()['total'] == 1
        assert client.post(base + '/resume', headers=headers,
                           json={'decision': 'approve'}).status_code == 409
        completed = client.post(base + '/resume', headers=headers,
                                json={'decision': 'retry'}).json()
        assert completed['status'] == 'completed'
        assert provider.plan_calls == provider.review_calls == provider.risk_calls == 1
        assert provider.supervisor_calls == worker.calls == 1
        assert publisher.calls == 2
        assert sum(e['title'] == 'Plan approved' for e in completed['timeline']) == 1


def test_live_index_failure_and_stale_backfill():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        class PausedProvider(RecordingProvider):
            async def create_plan(self, requirement):
                entered.set()
                await release.wait()
                raise RuntimeError('planner unavailable')

        index, saver = MemoryTaskIndex(), InMemorySaver()
        service = RunService(build_main_graph(PausedProvider(), RecordingWorker(), saver),
                             task_index=index)
        task = asyncio.create_task(service.start(StartRunRequest(
            project_id='demo', requirement='Failure during planning', production_line='B')))
        await asyncio.wait_for(entered.wait(), 5)
        live = (await index.list()).items[0]
        assert live.status == 'running' and live.stage == 'planning'
        release.set()
        with pytest.raises(RuntimeError, match='planner unavailable'):
            await task
        failed = await service.get(live.run_id)
        assert failed.status == 'failed'
        assert 'planner unavailable' in failed.blocking_reason['detail']
        assert failed.pending_action is None
        assert failed.workflow_steps[1]['state'] == 'blocked'
        assert failed.workflow_steps[2]['state'] == 'not_started'
        assert (await index.get(live.run_id)).status == 'failed'
        await index.upsert(dict(run_id=live.run_id, project_id='demo', requirement='stale',
                                current_stage='intake', status='running'))
        await service.backfill(saver)
        repaired = await index.get(live.run_id)
        assert repaired.status == 'failed' and repaired.production_line == 'B'
        await service.backfill(saver)
        assert (await index.get(live.run_id)).updated_at == repaired.updated_at
    asyncio.run(scenario())


def test_terminal_steps_and_pagination():
    for stage in ('failed', 'rejected'):
        steps = workflow_steps(dict(current_stage=stage, status=stage,
                                    timeline=[{'stage': 'plan_approval'}]))
        assert steps[2]['state'] == 'blocked'
        assert all(step['state'] == 'not_started' for step in steps[3:])
    assert all(step['state'] == 'not_started' for step in workflow_steps(
        dict(current_stage='failed', status='failed')))

    async def scenario():
        index = MemoryTaskIndex()
        for number in range(55):
            await index.upsert(dict(run_id=str(number), project_id='demo', requirement='task',
                                    current_stage='planning', status='running'))
        first, second = await index.list(), await index.list(page=2)
        assert len(first.items) == 50 and len(second.items) == 5
        assert len({item.run_id for item in first.items + second.items}) == 55
    asyncio.run(scenario())


def test_legacy_string_implementation_is_projected_as_execution_result():
    snapshot = SimpleNamespace(
        values={"implementation": "worker=local legacy result"}, tasks=[], next=[]
    )

    values = checkpoint_values(snapshot)

    assert values["implementation"].summary == "worker=local legacy result"
