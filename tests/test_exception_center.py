import pytest

from taskhub_v2.persistence.task_index import MemoryTaskIndex
from taskhub_v2.services.exceptions import ExceptionCenterService


class KnownProjects:
    def __init__(self, *project_ids):
        self.project_ids = set(project_ids)

    def get(self, project_id):
        if project_id not in self.project_ids:
            raise LookupError(project_id)
        return project_id


async def add_task(index, run_id, *, project_id="shop", stage, status, **values):
    await index.upsert(
        {
            "run_id": run_id,
            "project_id": project_id,
            "requirement": f"Requirement {run_id}",
            "production_line": "default",
            "current_stage": stage,
            "status": status,
            "blocking_reason": values.get("blocking_reason"),
            "pending_action": values.get("pending_action"),
        }
    )


@pytest.mark.anyio
async def test_exception_center_projects_authoritative_task_attention(tmp_path):
    index = MemoryTaskIndex()
    projects = KnownProjects("shop")
    await add_task(
        index,
        "recoverable",
        stage="acceptance_blocked",
        status="blocked",
        pending_action={
            "type": "acceptance_recovery",
            "title": "验收执行失败",
            "detail": "pytest returned exit 1",
            "choices": ["retry", "revise", "cancel"],
        },
    )
    await add_task(
        index,
        "failed",
        stage="failed",
        status="failed",
        blocking_reason={"code": "workflow_error", "detail": "checkpoint error"},
    )
    await add_task(index, "healthy", stage="implementation", status="running")

    report = await ExceptionCenterService(index, projects).list()

    assert report["total"] == 2
    assert report["summary"] == {
        "waiting": 0,
        "blocked": 1,
        "failed": 1,
        "visible_recoverable": 1,
    }
    by_id = {item["run_id"]: item for item in report["items"]}
    assert by_id["recoverable"]["category"] == "acceptance"
    assert by_id["recoverable"]["actions"][0] == {
        "decision": "retry",
        "label": "重试当前环节",
    }
    assert by_id["failed"]["severity"] == "critical"
    assert by_id["failed"]["recoverable"] is False


@pytest.mark.anyio
async def test_exception_center_turns_orphaned_tasks_into_rebind_actions(tmp_path):
    index = MemoryTaskIndex()
    projects = KnownProjects()
    await add_task(
        index,
        "orphaned",
        project_id="removed-project",
        stage="implementation_blocked",
        status="blocked",
    )

    report = await ExceptionCenterService(index, projects).list()
    item = report["items"][0]

    assert item["category"] == "project"
    assert item["title"] == "项目已失去绑定"
    assert [action["decision"] for action in item["actions"]] == [
        "rebind_project",
        "archive",
    ]
