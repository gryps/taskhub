import asyncio
import json
import subprocess
from pathlib import Path
from uuid import uuid4

import httpx

from taskhub_v2.artifacts import ArtifactStore
from taskhub_v2.browser import PreviewManager, load_acceptance_contract
from taskhub_v2.browser.contract import PREPRODUCTION_EXAMPLE, load_acceptance_suite
from taskhub_v2.browser.reports import setup_failure_detail, validate_junit
from taskhub_v2.domain.models import AcceptanceEvidence, AcceptanceResult
from taskhub_v2.projects import ProjectRegistry
from taskhub_v2.workers import acceptance_checkpoint as checkpoint
from taskhub_v2.workers import contract_acceptance as evidence
from taskhub_v2.workers import supplemental_acceptance
from taskhub_v2.workers.acceptance_support import artifact_kind, is_browser_contract


class AcceptanceExecutionError(RuntimeError):
    reason = "acceptance_failed"

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class PreproductionContractRequiredError(AcceptanceExecutionError):
    reason = "preproduction_contract_missing"


class PreproductionVerificationError(AcceptanceExecutionError):
    reason = "preproduction_verification_failed"


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
        project_contracts=None,
    ):
        self.projects = projects
        self.scheduler = scheduler
        self.artifacts = artifacts
        self.preview_manager = preview_manager
        self.project_contracts = project_contracts

    async def verify(self, run_id, project_id, implementation) -> AcceptanceResult:
        project = self.projects.get(project_id)
        records = await supplemental_acceptance.verify(
            self.project_contracts, self.artifacts, self.scheduler, run_id,
            project, implementation, AcceptanceExecutionError,
        )
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
            checkpoint_fingerprint = checkpoint.acceptance_checkpoint_fingerprint(
                project, implementation
            )
            saved_checkpoint = checkpoint.load_acceptance_checkpoint(
                self.artifacts,
                run_id,
                checkpoint_fingerprint,
                {checkpoint.legacy_acceptance_checkpoint_fingerprint(project, implementation)},
            )
            if saved_checkpoint:
                records.extend(saved_checkpoint)
            else:
                scheduled = await self.scheduler.run(
                    f"{run_id}-acceptance",
                    f"{run_id}-acceptance",
                    project.acceptance_commands,
                    project.test_timeout_seconds,
                    implementation.workspace.path,
                    workload="acceptance",
                    required_capabilities_override=project.acceptance_capabilities,
                    execution_environment=(
                        project.test_environment.execution_environment()
                        if project.test_environment
                        else {}
                    ),
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
                project_evidence = [
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
                ]
                if "test_database" in project.acceptance_capabilities:
                    project_evidence.append(
                        evidence.database_acceptance_evidence(scheduled, failed)
                    )
                records.extend(project_evidence)
                if failed:
                    raise AcceptanceExecutionError(failed[0].output_tail)
                checkpoint.write_acceptance_checkpoint(
                    self.artifacts,
                    run_id,
                    checkpoint_fingerprint,
                    project_evidence,
                )
        contract_path = (
            Path(implementation.workspace.path) / ".taskhub" / "acceptance.yaml"
            if implementation.workspace
            else Path()
        )
        if contract_path.is_file() and is_browser_contract(contract_path):
            if not implementation.workspace or not implementation.commit:
                raise AcceptanceExecutionError("browser acceptance requires a committed workspace")
            contract = load_acceptance_contract(implementation.workspace.path)
            suite = load_acceptance_suite(implementation.workspace.path, contract)
            actual_commit = _browser_acceptance_commit(
                implementation.workspace.path, implementation.commit
            )
            if project.test_environment and contract.target != "preproduction":
                raise PreproductionContractRequiredError(
                    "项目已配置托管预生产环境，验收契约必须使用 target: preproduction，"
                    "并提供 preproduction.prepare_command。该命令由编码模型实现，"
                    "TaskHub 自动执行；不得要求使用者手工部署或补交证据。\n"
                    f"契约结构：\n{PREPRODUCTION_EXAMPLE}\n"
                    "健康接口必须返回 git_commit、environment 和 database_revision。"
                )
            if contract.target == "preproduction" and not project.test_environment:
                raise PreproductionContractRequiredError(
                    "验收契约要求预生产目标，但项目尚未配置预生产主机"
                )
            browser_commands = [*contract.setup_commands, contract.command]
            await self.scheduler.preflight_browser(
                browser_commands, contract.required_capabilities
            )
            preview_id = f"{run_id}-{uuid4().hex[:8]}"
            preview = None
            target_url = ""
            execution_environment = (
                project.test_environment.execution_environment() if project.test_environment else {}
            )
            try:
                if contract.target == "preproduction":
                    prepared, health = await self._prepare_preproduction(
                        run_id, project, implementation.workspace.path, actual_commit, contract
                    )
                    target_url = project.test_environment.target_url
                    records.append(
                        AcceptanceEvidence(
                            id="preproduction-deployment",
                            kind=(
                                "database"
                                if contract.preproduction.expected_database_revision
                                else "other"
                            ),
                            status="passed",
                            source=prepared.node_id,
                            summary=(
                                f"Candidate {actual_commit} deployed to {target_url}; "
                                f"environment {health['environment']} and database revision "
                                f"{health['database_revision']} verified"
                            ),
                            tests=prepared.tests,
                        )
                    )
                else:
                    if self.preview_manager is None:
                        raise AcceptanceExecutionError("browser preview manager is not configured")
                    preview = await self.preview_manager.start(
                        preview_id, implementation.workspace.path, actual_commit, contract.preview
                    )
                    target_url = preview.url
                browser_job_id = f"{run_id}-browser-{uuid4().hex[:8]}"
                scheduled = await self.scheduler.run(
                    browser_job_id,
                    f"{run_id}-browser",
                    browser_commands,
                    contract.timeout_seconds,
                    implementation.workspace.path,
                    workload=contract.workload,
                    required_capabilities_override=contract.required_capabilities,
                    target_url=target_url,
                    git_commit=actual_commit,
                    artifact_paths=contract.required_artifacts,
                    execution_environment=execution_environment,
                )
                if scheduled.metadata.get("git_commit") != actual_commit:
                    raise AcceptanceExecutionError("browser evidence commit mismatch")
                if scheduled.metadata.get("target_url") != target_url:
                    raise AcceptanceExecutionError("browser evidence target URL mismatch")
                setup_failure = setup_failure_detail(scheduled.tests, len(contract.setup_commands))
                if setup_failure:
                    raise AcceptanceExecutionError("browser setup failed: " + setup_failure)
                browser_artifacts = []
                junit_reports = []
                for item in scheduled.metadata.pop("downloaded_artifacts", []):
                    if item["path"].endswith(".xml"):
                        junit_reports.append(item["content"])
                    browser_artifacts.append(
                        self.artifacts.write_bytes(
                            run_id,
                            item["path"].replace("/", "-"),
                            artifact_kind(item["path"]),
                            item["content"],
                            expected_sha256=item["sha256"],
                            metadata={**scheduled.metadata, "original_path": item["path"]},
                        )
                    )
                found = {item.metadata.get("original_path") for item in browser_artifacts}
                failed = [test for test in scheduled.tests if test.exit_code]
                try:
                    validate_junit(
                        junit_reports,
                        contract.browsers,
                        target_url=target_url,
                        git_commit=actual_commit,
                        scenarios={item.id: item.browsers for item in suite.scenarios},
                    )
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
                    raise AcceptanceExecutionError(
                        "required browser artifacts missing: " + ", ".join(missing)
                    )
                records.append(
                    AcceptanceEvidence(
                        id="windows-browser-acceptance",
                        kind="browser",
                        status="failed" if failed else "passed",
                        source=scheduled.node_id,
                        summary=(
                            f"Chromium and Edge acceptance at {target_url} for {actual_commit}; "
                            "zero failures and skips; verified scenarios: "
                            + ", ".join(item.id for item in suite.scenarios)
                        ),
                        tests=scheduled.tests,
                        artifacts=browser_artifacts,
                    )
                )
                if failed:
                    raise AcceptanceExecutionError(failed[0].output_tail)
            finally:
                if preview is not None:
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

    async def _prepare_preproduction(self, run_id, project, workspace: str, commit: str, contract):
        specification = contract.preproduction
        environment = project.test_environment.execution_environment()
        scheduled = await self.scheduler.run(
            f"{run_id}-preproduction-{uuid4().hex[:8]}",
            f"{run_id}-preproduction",
            [specification.prepare_command],
            specification.timeout_seconds,
            workspace,
            workload="acceptance",
            required_capabilities_override=project.acceptance_capabilities,
            git_commit=commit,
            execution_environment=environment,
        )
        failed = [test for test in scheduled.tests if test.exit_code]
        if failed:
            raise PreproductionVerificationError(failed[0].output_tail)
        health = await _wait_for_preproduction(project.test_environment, specification, commit)
        return scheduled, health


async def _wait_for_preproduction(environment, specification, commit: str) -> dict:
    url = environment.target_url + specification.health_path
    deadline = asyncio.get_running_loop().time() + specification.timeout_seconds
    last_detail = "预生产健康检查超时"
    async with httpx.AsyncClient(follow_redirects=False) as client:
        while asyncio.get_running_loop().time() < deadline:
            try:
                response = await client.get(url, timeout=5)
                response.raise_for_status()
                payload = response.json()
                actual_commit = payload.get(specification.commit_field)
                actual_environment = payload.get(specification.environment_field)
                database_revision = payload.get(specification.database_revision_field)
                if actual_commit != commit:
                    last_detail = (
                        f"预生产提交不匹配：期望 {commit}，实际 {actual_commit or '未上报'}"
                    )
                elif actual_environment != environment.expected_environment:
                    last_detail = (
                        "预生产环境标识不匹配：期望 "
                        f"{environment.expected_environment}，实际 {actual_environment or '未上报'}"
                    )
                elif (
                    specification.expected_database_revision
                    and database_revision != specification.expected_database_revision
                ):
                    last_detail = (
                        "数据库迁移版本不匹配：期望 "
                        f"{specification.expected_database_revision}，"
                        f"实际 {database_revision or '未上报'}"
                    )
                else:
                    return {
                        "git_commit": actual_commit,
                        "environment": actual_environment,
                        "database_revision": database_revision or "not-required",
                    }
            except (httpx.HTTPError, ValueError) as exc:
                last_detail = f"预生产健康检查失败：{type(exc).__name__}"
            await asyncio.sleep(1)
    raise PreproductionVerificationError(last_detail)


def _browser_acceptance_commit(worktree: str, expected_commit: str) -> str:
    actual_commit = subprocess.run(
        ["git", "-C", worktree, "rev-parse", "--verify", "HEAD^{commit}"],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    ).stdout.strip()
    if actual_commit == expected_commit:
        return actual_commit
    ancestor = subprocess.run(
        ["git", "-C", worktree, "merge-base", "--is-ancestor", expected_commit, actual_commit],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if ancestor.returncode == 0:
        return actual_commit
    raise AcceptanceExecutionError("browser acceptance commit does not match workspace HEAD")
