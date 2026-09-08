import json
import subprocess
from pathlib import Path
from uuid import uuid4

from taskhub_v2.artifacts import ArtifactStore
from taskhub_v2.browser import PreviewManager, load_acceptance_contract
from taskhub_v2.browser.contract import load_acceptance_suite
from taskhub_v2.browser.reports import validate_junit
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
    def __init__(
        self,
        projects: ProjectRegistry,
        scheduler,
        artifacts: ArtifactStore,
        preview_manager: PreviewManager | None = None,
    ):
        self.projects = projects
        self.scheduler = scheduler
        self.artifacts = artifacts
        self.preview_manager = preview_manager

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
                required_capabilities_override=project.acceptance_capabilities,
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
        contract_path = (
            Path(implementation.workspace.path) / ".taskhub" / "acceptance.yaml"
            if implementation.workspace
            else Path()
        )
        if contract_path.is_file():
            if not implementation.workspace or not implementation.commit:
                raise AcceptanceExecutionError("browser acceptance requires a committed workspace")
            if self.preview_manager is None:
                raise AcceptanceExecutionError("browser preview manager is not configured")
            contract = load_acceptance_contract(implementation.workspace.path)
            suite = load_acceptance_suite(implementation.workspace.path, contract)
            actual_commit = _browser_acceptance_commit(
                implementation.workspace.path, implementation.commit
            )
            await self.scheduler.preflight_browser([contract.command], contract.required_capabilities)
            preview_id = f"{run_id}-{uuid4().hex[:8]}"
            preview = None
            try:
                preview = await self.preview_manager.start(
                    preview_id, implementation.workspace.path, actual_commit, contract.preview
                )
                browser_job_id = f"{run_id}-browser-{uuid4().hex[:8]}"
                scheduled = await self.scheduler.run(
                    browser_job_id, f"{run_id}-browser", [contract.command],
                    contract.timeout_seconds, implementation.workspace.path,
                    workload=contract.workload,
                    required_capabilities_override=contract.required_capabilities,
                    target_url=preview.url, git_commit=actual_commit,
                    artifact_paths=contract.required_artifacts,
                )
                if scheduled.metadata.get("git_commit") != actual_commit:
                    raise AcceptanceExecutionError("browser evidence commit mismatch")
                if scheduled.metadata.get("target_url") != preview.url:
                    raise AcceptanceExecutionError("browser evidence target URL mismatch")
                browser_artifacts = []
                junit_reports = []
                for item in scheduled.metadata.pop("downloaded_artifacts", []):
                    if item["path"].endswith(".xml"):
                        junit_reports.append(item["content"])
                    browser_artifacts.append(self.artifacts.write_bytes(
                        run_id, item["path"].replace("/", "-"), _artifact_kind(item["path"]),
                        item["content"], expected_sha256=item["sha256"],
                        metadata={**scheduled.metadata, "original_path": item["path"]},
                    ))
                found = {item.metadata.get("original_path") for item in browser_artifacts}
                failed = [test for test in scheduled.tests if test.exit_code]
                try:
                    validate_junit(junit_reports, contract.browsers,
                                   target_url=preview.url, git_commit=actual_commit,
                                   scenarios={item.id: item.browsers for item in suite.scenarios})
                except ValueError as exc:
                    if failed and str(exc) == "browser acceptance requires executed test cases":
                        raise AcceptanceExecutionError(failed[0].output_tail or str(exc)) from exc
                    raise AcceptanceExecutionError(str(exc)) from exc
                missing = [
                    name
                    for name in contract.required_artifacts
                    if not any(
                        path == name or str(path).startswith(name.rstrip("/") + "/")
                        for path in found
                    )
                ]
                if missing:
                    raise AcceptanceExecutionError("required browser artifacts missing: " + ", ".join(missing))
                records.append(AcceptanceEvidence(
                    id="windows-browser-acceptance", kind="browser",
                    status="failed" if failed else "passed", source=scheduled.node_id,
                    summary=(
                        f"Chromium and Edge acceptance at {preview.url} for {actual_commit}; "
                        "zero failures and skips; verified scenarios: "
                        + ", ".join(item.id for item in suite.scenarios)
                    ),
                    tests=scheduled.tests, artifacts=browser_artifacts,
                ))
                if failed:
                    raise AcceptanceExecutionError(failed[0].output_tail)
            finally:
                await self.preview_manager.stop(preview_id)
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


def _artifact_kind(path: str) -> str:
    if path.endswith("trace.zip"):
        return "browser_trace"
    if path.endswith(".xml"):
        return "junit_report"
    if path.lower().endswith((".png", ".jpg", ".jpeg")):
        return "screenshot"
    if path.lower().endswith((".webm", ".mp4")):
        return "browser_video"
    return "playwright_report"


def _browser_acceptance_commit(worktree: str, expected_commit: str) -> str:
    actual_commit = subprocess.run(
        ["git", "-C", worktree, "rev-parse", "--verify", "HEAD^{commit}"],
        capture_output=True, text=True, check=True, timeout=15,
    ).stdout.strip()
    if actual_commit == expected_commit:
        return actual_commit
    ancestor = subprocess.run(
        ["git", "-C", worktree, "merge-base", "--is-ancestor", expected_commit, actual_commit],
        capture_output=True, text=True, timeout=15,
    )
    if ancestor.returncode == 0:
        return actual_commit
    raise AcceptanceExecutionError("browser acceptance commit does not match workspace HEAD")
