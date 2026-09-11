import asyncio
import json

from fastapi.testclient import TestClient

from taskhub_v2.api.app import _required_permission, create_app
from taskhub_v2.config import Settings
from taskhub_v2.domain.models import ProjectDefinition
from taskhub_v2.domain.production import ProductionTask, ProductionTaskStatus, TaskAttempt
from taskhub_v2.persistence.production import MemoryProductionStore
from taskhub_v2.services.dag_plan import DagPlanService
from taskhub_v2.services.dag_scheduler import PersistentDagScheduler
from taskhub_v2.services.revisions import RevisionConflictError, RevisionService
from tests.test_dag_orchestration import RecordingExecutor, compiled


class Projects:
    def get(self, project_id):
        if project_id != "demo":
            raise LookupError(project_id)
        return ProjectDefinition(id="demo", repository="/tmp", max_revision_attempts=2)


def test_local_change_reruns_only_affected_subgraph_and_reuses_evidence():
    async def scenario():
        store = MemoryProductionStore()
        original = await compiled(store)
        first_execution = RecordingExecutor()
        await PersistentDagScheduler(store, first_execution).execute(original.plan.plan_id)
        service = RevisionService(store, Projects(), DagPlanService(store))
        request = await service.propose(
            "demo",
            original.plan.plan_id,
            1,
            "Change only the API module",
            actor="developer",
            changed_paths=["src/api/users.py"],
        )
        assert set(request.affected_task_ids) == {
            original.tasks[0].task_id,
            original.tasks[2].task_id,
        }
        assert request.plan_diff["reuse"] == [original.tasks[1].task_id]
        request = await service.approve("demo", request.change_request_id, "owner")
        revised = await service.apply("demo", request.change_request_id, "owner")
        assert revised["execution_plan"].version == 2
        assert revised["execution_plan"].previous_plan_version == 1
        reused = next(item for item in revised["tasks"] if item.reused_from_task_id)
        assert reused.reused_from_task_id == original.tasks[1].task_id
        assert reused.status == ProductionTaskStatus.COMPLETED
        attempts = await store.list(project_id="demo", object_type="task_attempt")
        reused_attempt = next(
            item
            for item in attempts
            if isinstance(item, TaskAttempt) and item.task_id == reused.task_id
        )
        assert reused_attempt.reused_from_attempt_id
        second_execution = RecordingExecutor()
        await PersistentDagScheduler(store, second_execution).execute(
            original.plan.plan_id, version=2
        )
        assert len(second_execution.calls) == 2
        old_attempts = [
            item for item in attempts if isinstance(item, TaskAttempt) and item.plan_version == 1
        ]
        assert len(old_attempts) == 3

    asyncio.run(scenario())


def test_automatic_revision_limit_requires_manual_approval():
    async def scenario():
        store = MemoryProductionStore()
        original = await compiled(store)
        service = RevisionService(store, Projects(), DagPlanService(store))
        statuses = []
        for number in range(3):
            request = await service.propose(
                "demo",
                original.plan.plan_id,
                1,
                f"failure {number}",
                actor="scheduler",
                affected_task_ids=[original.tasks[0].task_id],
                automatic=True,
            )
            statuses.append(str(request.status))
        assert statuses == ["approved", "approved", "proposed"]
        requests = await service.list("demo")
        assert requests[0].plan_diff["requires_manual_approval"] is True

    asyncio.run(scenario())


def test_automatic_revision_preparation_cannot_bypass_limit():
    async def scenario():
        store = MemoryProductionStore()
        original = await compiled(store)
        service = RevisionService(store, Projects(), DagPlanService(store))
        for number in range(2):
            await service.propose(
                "demo",
                original.plan.plan_id,
                1,
                f"earlier failure {number}",
                actor="scheduler",
                affected_task_ids=[original.tasks[0].task_id],
                automatic=True,
            )
        try:
            await service.prepare_automatic("demo", original.plan.plan_id, 1, "another failure")
        except RevisionConflictError as error:
            assert "manual approval" in str(error)
        else:
            raise AssertionError("automatic revision bypassed the configured limit")
        requests = await service.list("demo")
        assert requests[0].status.value == "proposed"
        assert await store.get("execution_plan", original.plan.plan_id, "2") is None

    asyncio.run(scenario())


def test_revised_task_model_keeps_original_task_as_history():
    task = ProductionTask(
        project_id="demo",
        task_id="task_original",
        plan_id="plan_demo",
        plan_version=1,
        title="Original",
        objective="Original objective",
    )
    assert task.supersedes_task_id == "" and task.reused_from_task_id == ""


def test_change_request_api_is_csrf_protected_and_applies_revision(tmp_path):
    projects_file = tmp_path / "projects.json"
    projects_file.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "id": "demo",
                        "name": "Demo",
                        "repository": str(tmp_path),
                        "base_ref": "main",
                        "max_revision_attempts": 2,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    nodes_file = tmp_path / "nodes.json"
    nodes_file.write_text(
        json.dumps(
            {
                "nodes": [
                    {
                        "id": "controller-local",
                        "kind": "local",
                        "workloads": ["coding", "test"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    app = create_app(
        Settings(
            checkpointer="memory",
            provider="deterministic",
            admin_token="revision-admin",
            session_secret="revision-session",
            projects_file=str(projects_file),
            test_runner="scheduled",
            nodes_file=str(nodes_file),
            node_state_file=str(tmp_path / "node-state.json"),
            production_orchestration_enabled=True,
            operations_log_file=str(tmp_path / "operations.jsonl"),
        )
    )
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"token": "revision-admin"})
        headers = {"X-CSRF-Token": client.cookies.get("taskhub_v2_csrf")}
        original = asyncio.run(compiled(app.state.production_objects))
        payload = {
            "project_id": "demo",
            "plan_id": original.plan.plan_id,
            "plan_version": 1,
            "reason": "Update API path",
            "changed_paths": ["src/api/users.py"],
        }
        assert client.post("/api/change-requests", json=payload).status_code == 403
        created = client.post("/api/change-requests", headers=headers, json=payload)
        assert created.status_code == 201
        request_id = created.json()["change_request_id"]
        root = f"/api/change-requests/{request_id}"
        assert client.post(f"{root}/approve?project_id=demo", headers=headers).status_code == 200
        applied = client.post(f"{root}/apply?project_id=demo", headers=headers)
        assert applied.status_code == 200, applied.text
        assert applied.json()["execution_plan"]["version"] == 2
        assert (
            client.get("/api/change-requests?project_id=demo").json()["change_requests"][0][
                "status"
            ]
            == "applied"
        )
    assert _required_permission("/api/change-requests", "POST") == "delivery:execute"
