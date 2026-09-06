import asyncio

from fastapi.testclient import TestClient

from taskhub_v2.api.app import create_app
from taskhub_v2.config import Settings
from taskhub_v2.persistence.task_index import MemoryTaskIndex


def settings():
    return Settings(
        checkpointer="memory", admin_token="admin-secret", session_secret="session-secret"
    )


def login(client):
    response = client.post("/api/auth/login", json={"token": "admin-secret"})
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


def test_task_detail_exposes_backend_action_and_ten_stage_ui():
    with TestClient(create_app(settings())) as client:
        headers = login(client)
        run = client.post("/api/runs", headers=headers, json={
            "project_id": "demo", "requirement": "Build task details"
        }).json()
        detail = client.get(f"/api/runs/{run['run_id']}").json()
        assert detail["pending_action"]["choices"] == ["approve", "reject"]
        assert len(detail["workflow_steps"]) == 10
        assert detail["workflow_steps"][2]["state"] == "waiting_manual"
        assert detail["created_at"] and detail["updated_at"]
        html = client.get("/").text
        assert html.count("任务中心") >= 1
        script = client.get("/static/app.js").text
        assert "localStorage.setItem(\"taskhub_run_id\"" not in script
        for stage in ("intake", "planning", "plan_approval", "implementation", "review",
                      "risk", "supervision", "merge_approval", "merging", "completed"):
            assert f'"{stage}"' in script
