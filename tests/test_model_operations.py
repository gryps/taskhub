from datetime import UTC, datetime

import pytest

from taskhub_v2.domain.models import RunView, TaskPage, TaskSummary
from taskhub_v2.services.model_operations import ModelOperationsService


class FakeCatalog:
    async def status(self):
        return {
            "providers": [{
                "id": "primary",
                "display_name": "Primary Card",
                "kind": "api",
                "configured": True,
                "status": "cooldown:quota_exceeded",
                "model": "gpt-test",
                "runtime_health": {
                    "status": "cooldown",
                    "reason": "quota_exceeded",
                    "failure_count": 3,
                    "recovery_count": 0,
                    "retry_at": 1234,
                },
                "billing": {
                    "kind": "account_limits",
                    "status": "available",
                    "metrics": [{"label": "周消耗", "used_percent": 75}],
                },
            }],
            "role_models": {"planner": "gpt-test"},
        }


class FakeRuns:
    def __init__(self, run):
        self.run = run

    async def list(self, **filters):
        item = TaskSummary(
            run_id=self.run.run_id,
            requirement_summary=self.run.requirement,
            project_id=self.run.project_id,
            production_line=self.run.production_line,
            stage=self.run.stage,
            status=self.run.status,
            created_at=self.run.created_at,
            updated_at=self.run.updated_at,
        )
        return TaskPage(items=[item], total=1, page=1, page_size=filters["page_size"])

    async def get(self, run_id):
        assert run_id == self.run.run_id
        return self.run


@pytest.mark.anyio
async def test_model_operations_combines_health_quota_and_checkpoint_traces():
    now = datetime.now(UTC)
    run = RunView.model_validate({
        "run_id": "run-1",
        "project_id": "shop",
        "requirement": "Ship",
        "created_at": now,
        "updated_at": now,
        "stage": "completed",
        "status": "completed",
        "next_nodes": [],
        "model_runs": [
            {"role": "planner", "provider": "backup", "model": "gpt-test",
             "duration_ms": 1200, "failed_providers": ["primary:quota_exceeded"]},
            {"role": "coder", "provider": "backup", "model": "gpt-test",
             "duration_ms": 800, "failed_providers": []},
        ],
    })

    report = await ModelOperationsService(FakeCatalog(), FakeRuns(run)).report()

    assert report["summary"] == {
        "providers": 1,
        "unhealthy_providers": 1,
        "invocations": 2,
        "fallback_events": 1,
        "average_duration_ms": 1000,
        "sampled_runs": 1,
        "available_runs": 1,
    }
    assert report["providers"][0]["operational_state"] == "cooldown"
    assert report["providers"][0]["display_name"] == "Primary Card"
    assert report["providers"][0]["reason"] == "quota_exceeded"
    assert report["usage"][0]["roles"] == {"planner": 1, "coder": 1}
    assert report["usage"][0]["average_duration_ms"] == 1000
