from __future__ import annotations

import re
from typing import Any

from taskhub_v2.domain.models import RunStatus

EVIDENCE_LABELS = {
    "browser": "浏览器",
    "database": "数据库",
    "openapi": "OpenAPI",
    "test": "自动测试",
    "manual": "人工确认",
}


class EvidenceCenterService:
    """Read-only evidence projection over authoritative workflow checkpoints."""

    def __init__(self, runs):
        self.runs = runs

    async def list(self, *, project_id="", page=1, page_size=20) -> dict[str, Any]:
        task_page = await self.runs.list(
            project_id=project_id,
            page=page,
            page_size=page_size,
        )
        items = [self._project(await self.runs.get(item.run_id)) for item in task_page.items]
        return {
            "items": items,
            "total": task_page.total,
            "page": task_page.page,
            "page_size": task_page.page_size,
            "summary": {
                "verified": sum(item["state"] == "verified" for item in items),
                "collecting": sum(item["state"] == "collecting" for item in items),
                "missing": sum(item["state"] == "missing" for item in items),
                "failed": sum(item["state"] == "failed" for item in items),
            },
        }

    @classmethod
    def _project(cls, run) -> dict[str, Any]:
        implementation = run.implementation
        acceptance = run.acceptance
        supervision = run.supervision
        publication = run.publication
        implementation_tests = list(implementation.tests if implementation else [])
        acceptance_evidence = list(acceptance.evidence if acceptance else [])
        acceptance_tests = [
            test for evidence in acceptance_evidence for test in evidence.tests
        ]
        publication_tests = list(publication.tests if publication else [])
        tests = [*implementation_tests, *acceptance_tests, *publication_tests]
        artifacts = [
            *(implementation.artifacts if implementation else []),
            *(artifact for evidence in acceptance_evidence for artifact in evidence.artifacts),
        ]
        projected_artifacts = [cls._artifact(run.run_id, artifact) for artifact in artifacts]
        passed_tests = sum(test.exit_code == 0 for test in tests)
        passed_evidence = sum(item.status == "passed" for item in acceptance_evidence)
        failed = (
            any(test.exit_code != 0 for test in tests)
            or (acceptance is not None and acceptance.status == "failed")
            or any(item.status == "failed" for item in acceptance_evidence)
            or (supervision is not None and supervision.decision == "reject")
        )
        required_gates = {
            "implementation": implementation is not None,
            "acceptance": acceptance is not None and bool(acceptance_evidence),
            "supervision": supervision is not None and supervision.decision == "approve",
            "publication": publication is not None,
        }
        missing = list(dict.fromkeys(supervision.missing_evidence if supervision else []))
        invalid_artifacts = [item["name"] for item in projected_artifacts if not item["verified"]]
        missing_gates = [key for key, present in required_gates.items() if not present]
        completed = run.status == RunStatus.COMPLETED
        if failed:
            state = "failed"
        elif missing or invalid_artifacts or (completed and missing_gates):
            state = "missing"
        elif completed:
            state = "verified"
        else:
            state = "collecting"
        lineage = cls._lineage(run)
        return {
            "run_id": run.run_id,
            "project_id": run.project_id,
            "requirement": run.requirement,
            "stage": str(run.stage),
            "status": str(run.status),
            "updated_at": run.updated_at,
            "state": state,
            "completeness": sum(required_gates.values()) / len(required_gates),
            "integrity": (
                sum(item["verified"] for item in projected_artifacts) / len(projected_artifacts)
                if projected_artifacts
                else None
            ),
            "counts": {
                "tests": len(tests),
                "passed_tests": passed_tests,
                "evidence": len(acceptance_evidence),
                "passed_evidence": passed_evidence,
                "artifacts": len(projected_artifacts),
                "verified_artifacts": sum(item["verified"] for item in projected_artifacts),
            },
            "missing_evidence": [
                {"kind": item, "label": EVIDENCE_LABELS.get(item, item)} for item in missing
            ],
            "missing_gates": missing_gates if completed else [],
            "invalid_artifacts": invalid_artifacts,
            "commits": {
                "implementation": implementation.commit if implementation else None,
                "published": publication.published_commit if publication else None,
            },
            "artifacts": projected_artifacts,
            "lineage": lineage,
        }

    @staticmethod
    def _artifact(run_id: str, artifact) -> dict[str, Any]:
        name = artifact.uri.rsplit("/", 1)[-1]
        digest_valid = bool(re.fullmatch(r"[a-f0-9]{64}", artifact.sha256))
        explicitly_verified = artifact.metadata.get("verified") is True
        local_uri = artifact.uri.startswith(f"artifact://{run_id}/")
        return {
            "name": name,
            "kind": artifact.kind,
            "uri": artifact.uri,
            "sha256": artifact.sha256,
            "bytes": artifact.metadata.get("bytes"),
            "verified": digest_valid and explicitly_verified,
            "download_url": (
                f"/api/runs/{run_id}/artifacts/{name}" if local_uri else None
            ),
        }

    @staticmethod
    def _lineage(run) -> list[dict[str, Any]]:
        lines = []
        implementation = run.implementation
        if implementation:
            lines.append({
                "kind": "implementation",
                "source": implementation.coding_node or implementation.execution_node or "Seed",
                "status": (
                    "failed" if any(item.exit_code != 0 for item in implementation.tests)
                    else "passed"
                ),
                "summary": implementation.summary,
                "commit": implementation.commit,
            })
        if run.acceptance:
            lines.extend({
                "kind": item.kind,
                "source": item.source,
                "status": item.status,
                "summary": item.summary,
                "commit": None,
            } for item in run.acceptance.evidence)
        if run.supervision:
            lines.append({
                "kind": "supervision",
                "source": "TaskHub supervisor",
                "status": "passed" if run.supervision.decision == "approve" else "failed",
                "summary": run.supervision.summary,
                "commit": None,
            })
        if run.publication:
            lines.append({
                "kind": "publication",
                "source": run.publication.authority_ref,
                "status": "passed",
                "summary": f"Published {run.publication.branch}",
                "commit": run.publication.published_commit,
            })
        return lines
