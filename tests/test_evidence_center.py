from datetime import UTC, datetime

import pytest

from taskhub_v2.domain.models import RunView, TaskPage, TaskSummary
from taskhub_v2.services.evidence import EvidenceCenterService


class FakeRuns:
    def __init__(self, *runs):
        self.runs = {run.run_id: run for run in runs}

    async def list(self, **filters):
        project_id = filters.get("project_id")
        runs = [
            run for run in self.runs.values()
            if not project_id or run.project_id == project_id
        ]
        items = [TaskSummary(
            run_id=run.run_id,
            requirement_summary=run.requirement,
            project_id=run.project_id,
            production_line=run.production_line,
            stage=run.stage,
            status=run.status,
            created_at=run.created_at,
            updated_at=run.updated_at,
        ) for run in runs]
        return TaskPage(
            items=items,
            total=len(items),
            page=filters["page"],
            page_size=filters["page_size"],
        )

    async def get(self, run_id):
        return self.runs[run_id]


def run_view(run_id, **updates):
    now = datetime.now(UTC)
    values = {
        "run_id": run_id,
        "project_id": "shop",
        "requirement": f"Requirement {run_id}",
        "production_line": "default",
        "created_at": now,
        "updated_at": now,
        "stage": "completed",
        "status": "completed",
        "next_nodes": [],
    }
    values.update(updates)
    return RunView.model_validate(values)


@pytest.mark.anyio
async def test_evidence_center_projects_verified_lineage_and_artifact_digest():
    digest = "a" * 64
    run = run_view(
        "verified-run",
        implementation={
            "summary": "Implemented",
            "commit": "1" * 40,
            "tests": [{"command": ["pytest"], "exit_code": 0, "output_tail": "ok"}],
            "artifacts": [{
                "kind": "test_report",
                "uri": "artifact://verified-run/junit.xml",
                "sha256": digest,
                "metadata": {"verified": True, "bytes": 42},
            }],
            "coding_node": "seed-local",
        },
        acceptance={
            "status": "passed",
            "evidence": [{
                "id": "browser",
                "kind": "browser",
                "status": "passed",
                "source": "chrome",
                "summary": "Responsive acceptance passed",
            }],
        },
        supervision={
            "decision": "approve",
            "summary": "Approved",
            "reasons": [],
            "missing_evidence": [],
        },
        publication={
            "project_id": "shop",
            "authority_ref": "origin/main",
            "previous_commit": "0" * 40,
            "published_commit": "1" * 40,
            "branch": "main",
        },
    )

    report = await EvidenceCenterService(FakeRuns(run)).list()
    item = report["items"][0]

    assert report["summary"]["verified"] == 1
    assert item["state"] == "verified"
    assert item["completeness"] == 1
    assert item["integrity"] == 1
    assert item["artifacts"][0]["download_url"].endswith("/junit.xml")
    assert [entry["kind"] for entry in item["lineage"]] == [
        "implementation", "browser", "supervision", "publication",
    ]


@pytest.mark.anyio
async def test_evidence_center_distinguishes_missing_and_failed_evidence():
    missing = run_view(
        "missing-run",
        supervision={
            "decision": "approve",
            "summary": "Needs database rehearsal",
            "reasons": [],
            "missing_evidence": ["database"],
        },
    )
    failed = run_view(
        "failed-run",
        stage="acceptance_blocked",
        status="blocked",
        acceptance={
            "status": "failed",
            "evidence": [{
                "id": "tests",
                "kind": "test",
                "status": "failed",
                "source": "seed-local",
                "summary": "pytest failed",
            }],
        },
    )

    report = await EvidenceCenterService(FakeRuns(missing, failed)).list()
    by_id = {item["run_id"]: item for item in report["items"]}

    assert by_id["missing-run"]["state"] == "missing"
    assert by_id["missing-run"]["missing_evidence"] == [
        {"kind": "database", "label": "数据库"}
    ]
    assert by_id["missing-run"]["missing_gates"] == [
        "implementation", "acceptance", "publication",
    ]
    assert by_id["failed-run"]["state"] == "failed"
