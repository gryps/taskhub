import json

from taskhub_v2.artifacts import ArtifactStore
from taskhub_v2.domain.models import AcceptanceEvidence, AcceptanceResult
from taskhub_v2.projects import ProjectRegistry


class AcceptanceExecutionError(RuntimeError):
    reason = "acceptance_failed"

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class LocalAcceptanceGateway:
    """Use implementation tests as the acceptance record for non-Git workflows."""

    async def verify(self, run_id, project_id, implementation) -> AcceptanceResult:
        failed = [test for test in implementation.tests if test.exit_code]
        evidence = AcceptanceEvidence(
            id="implementation-tests",
            kind="test",
            status="failed" if failed else "passed",
            source=implementation.execution_node or "controller-local",
            summary=(
                f"{len(implementation.tests) - len(failed)}/{len(implementation.tests)} "
                "implementation tests passed"
            ),
            tests=implementation.tests,
            artifacts=implementation.artifacts,
        )
        return AcceptanceResult(status=evidence.status, evidence=[evidence])


class ProjectAcceptanceGateway:
    def __init__(self, projects: ProjectRegistry, scheduler, artifacts: ArtifactStore):
        self.projects = projects
        self.scheduler = scheduler
        self.artifacts = artifacts

    async def verify(self, run_id, project_id, implementation) -> AcceptanceResult:
        project = self.projects.get(project_id)
        records = []
        if implementation.tests:
            records.append(
                AcceptanceEvidence(
                    id="implementation-tests",
                    kind="test",
                    status="passed",
                    source=implementation.execution_node or "controller-local",
                    summary=f"{len(implementation.tests)} implementation tests passed",
                    tests=implementation.tests,
                    artifacts=implementation.artifacts,
                )
            )
        if project.acceptance_commands:
            if not implementation.workspace:
                raise AcceptanceExecutionError("acceptance requires a Git workspace")
            scheduled = await self.scheduler.run(
                f"{run_id}-acceptance",
                f"{run_id}-acceptance",
                project.acceptance_commands,
                project.test_timeout_seconds,
                implementation.workspace.path,
                workload="acceptance",
            )
            failed = [test for test in scheduled.tests if test.exit_code]
            artifact = self.artifacts.write_text(
                run_id,
                "acceptance.json",
                "acceptance_report",
                json.dumps(
                    [test.model_dump(mode="json") for test in scheduled.tests],
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            records.append(
                AcceptanceEvidence(
                    id="project-acceptance",
                    kind="test",
                    status="failed" if failed else "passed",
                    source=scheduled.node_id,
                    summary=(
                        f"{len(scheduled.tests) - len(failed)}/{len(scheduled.tests)} "
                        "acceptance commands passed"
                    ),
                    tests=scheduled.tests,
                    artifacts=[artifact],
                )
            )
            if failed:
                raise AcceptanceExecutionError(failed[0].output_tail)
        if not records:
            records.append(
                AcceptanceEvidence(
                    id="implementation-record",
                    kind="other",
                    status="passed",
                    source=implementation.coding_node or "controller-local",
                    summary="Implementation commit and diff recorded",
                    artifacts=implementation.artifacts,
                )
            )
        return AcceptanceResult(status="passed", evidence=records)
