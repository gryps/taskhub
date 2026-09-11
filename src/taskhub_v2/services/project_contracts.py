from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from taskhub_v2.domain.project_contract import (
    ManualReviewRule,
    ProjectContract,
    ProjectContractStatus,
)
from taskhub_v2.persistence.production import ProductionStore
from taskhub_v2.projects import ProjectNotFoundError, ProjectRegistry
from taskhub_v2.services.contract_documents import render_contract_documents
from taskhub_v2.services.contract_gate_models import (
    GateFinding,
    ProjectContractGateError,
    ProjectGateReport,
)
from taskhub_v2.services.contract_gates import (
    validate_repository,
)
from taskhub_v2.services.project_profiles import detect_profile, profile_catalog


class ProjectContractNotFoundError(LookupError):
    pass


class ProjectContractConflictError(RuntimeError):
    pass


class ProjectContractService:
    def __init__(self, store: ProductionStore, projects: ProjectRegistry, scheduler=None):
        self.store = store
        self.projects = projects
        self.scheduler = scheduler

    def profiles(self) -> list[dict]:
        return [profile.view() for profile in profile_catalog().values()]

    async def list(self, project_id: str) -> list[ProjectContract]:
        self._project(project_id)
        records = await self.store.list(project_id=project_id, object_type="project_contract")
        return sorted(
            (item for item in records if isinstance(item, ProjectContract)),
            key=lambda item: item.version,
            reverse=True,
        )

    async def current(self, project_id: str) -> ProjectContract | None:
        contracts = await self.list(project_id)
        return contracts[0] if contracts else None

    async def active(self, project_id: str) -> ProjectContract:
        active = [
            item
            for item in await self.list(project_id)
            if item.status == ProjectContractStatus.ACTIVE
        ]
        if not active:
            raise ProjectContractConflictError("项目合同尚未批准生效")
        return active[0]

    async def create_draft(
        self,
        project_id: str,
        *,
        profile_id: str | None,
        inferred: bool,
        actor: str,
        force_revision: bool = False,
    ) -> ProjectContract:
        project = self._project(project_id)
        repository = Path(project.repository).resolve()
        profiles = profile_catalog()
        selected = profile_id or detect_profile(repository)
        if selected not in profiles:
            raise ProjectContractConflictError("未知项目档案")
        previous = await self.current(project_id)
        if (
            previous
            and previous.status
            in {
                ProjectContractStatus.DRAFT,
                ProjectContractStatus.IN_REVIEW,
            }
            and not force_revision
        ):
            return previous
        if previous and previous.status != ProjectContractStatus.ACTIVE:
            raise ProjectContractConflictError("当前合同版本尚未完成治理")
        profile = profiles[selected]
        version = previous.version + 1 if previous else 1
        manual_review = []
        if inferred:
            manual_review.append(
                ManualReviewRule(
                    rule_id="confirm_inferred_boundaries",
                    description="确认扫描推断的技术栈、目录和模块边界",
                    required_evidence="项目负责人确认现有仓库扫描结果符合实际架构。",
                )
            )
        commands = profile.commands.model_copy(deep=True)
        if project.test_commands:
            commands.test = project.test_commands
        if project.acceptance_commands:
            commands.acceptance = project.acceptance_commands
        contract = ProjectContract(
            project_id=project_id,
            contract_id=f"pc_{project_id}",
            version=version,
            previous_version=previous.version if previous else None,
            profile_id=selected,
            inferred=inferred,
            repository_commit=self._commit(repository),
            languages=list(profile.languages),
            frameworks=list(profile.frameworks),
            directory_structure=list(profile.directories),
            modules=[item.model_copy(deep=True) for item in profile.modules],
            commands=commands,
            migrations=profile.migrations.model_copy(deep=True),
            artifacts=profile.artifacts.model_copy(deep=True),
            documentation_files=list(profile.documentation_files),
            manual_review=manual_review,
            created_by=actor,
            source_ids=[f"profile:{selected}", self._commit(repository)],
        )
        return await self.store.save(contract)

    async def update_draft(
        self, project_id: str, contract_id: str, version: int, changes: dict[str, Any]
    ) -> ProjectContract:
        contract = await self._contract(project_id, contract_id, version)
        if contract.status not in {
            ProjectContractStatus.DRAFT,
            ProjectContractStatus.IN_REVIEW,
        }:
            raise ProjectContractConflictError("已生效或已废弃的项目合同不可修改")
        allowed = {
            "languages",
            "frameworks",
            "directory_structure",
            "modules",
            "interfaces",
            "commands",
            "migrations",
            "artifacts",
            "repository_policy",
            "documentation_files",
            "environment_example",
            "manual_review",
            "manual_evidence",
        }
        invalid = set(changes) - allowed
        if invalid:
            raise ProjectContractConflictError("以下字段不可编辑：" + ", ".join(sorted(invalid)))
        return await self.store.save(self._replace(contract, **changes))

    async def submit_review(
        self, project_id: str, contract_id: str, version: int
    ) -> ProjectContract:
        contract = await self._contract(project_id, contract_id, version)
        if contract.status != ProjectContractStatus.DRAFT:
            raise ProjectContractConflictError("只有合同草稿可以提交评审")
        return await self.store.save(
            self._replace(contract, status=ProjectContractStatus.IN_REVIEW)
        )

    async def activate(
        self, project_id: str, contract_id: str, version: int, actor: str
    ) -> ProjectContract:
        contract = await self._contract(project_id, contract_id, version)
        if contract.status != ProjectContractStatus.IN_REVIEW:
            raise ProjectContractConflictError("只有待评审合同可以批准生效")
        for current in await self.list(project_id):
            if current.status == ProjectContractStatus.ACTIVE:
                await self.store.save(
                    self._replace(current, status=ProjectContractStatus.SUPERSEDED)
                )
        approved_at = datetime.now(UTC).isoformat()
        manual_evidence = dict(contract.manual_evidence)
        if contract.inferred:
            manual_evidence.setdefault(
                "confirm_inferred_boundaries",
                f"{actor} 于 {approved_at} 批准扫描推断的项目边界",
            )
        return await self.store.save(
            self._replace(
                contract,
                status=ProjectContractStatus.ACTIVE,
                approved_by=actor,
                approved_at=approved_at,
                manual_evidence=manual_evidence,
            )
        )

    async def gate(
        self,
        project_id: str,
        *,
        workspace: str | None = None,
        execute_commands: bool = True,
        strict: bool = True,
        manual_evidence: dict[str, str] | None = None,
        job_id: str | None = None,
    ) -> ProjectGateReport:
        contract = await self.active(project_id)
        project = self._project(project_id)
        target = workspace or project.repository
        target_path = Path(target)
        report = validate_repository(
            target,
            contract,
            strict=strict,
            manual_evidence={**contract.manual_evidence, **(manual_evidence or {})},
        )
        commands = contract.commands.gate_commands()
        if execute_commands and commands and report.status != "failed":
            if self.scheduler is None:
                raise ProjectContractConflictError("项目合同命令执行器未配置")
            scheduled = await self.scheduler.run(
                job_id or f"contract-{project_id}-{uuid4().hex[:12]}",
                f"contract-{project_id}",
                commands,
                project.test_timeout_seconds,
                target,
                workload="test",
                git_commit=self._commit(target),
                artifact_paths=contract.artifacts.required_artifacts,
            )
            for index, result in enumerate(scheduled.tests):
                command = " ".join(result.command)
                report.findings.append(
                    GateFinding(
                        gate_id=f"command:{index}",
                        category="command",
                        status="passed" if result.exit_code == 0 else "failed",
                        summary=(
                            f"合同命令 {'通过' if result.exit_code == 0 else '失败'}：{command}"
                        ),
                        detail=result.output_tail[-2_000:],
                    )
                )
            downloaded = {
                item.get("path", "") for item in scheduled.metadata.get("downloaded_artifacts", [])
            }
            for pattern in contract.artifacts.required_artifacts:
                unsafe = Path(pattern).is_absolute() or ".." in Path(pattern).parts
                present = not unsafe and (
                    any(target_path.glob(pattern))
                    or any(Path(path).match(pattern) for path in downloaded)
                )
                report.findings.append(
                    GateFinding(
                        gate_id=f"artifact-output:{pattern}",
                        category="artifact",
                        status="passed" if present else "failed",
                        summary=(
                            f"交付物已生成：{pattern}"
                            if present
                            else f"缺少声明的交付物：{pattern}"
                        ),
                    )
                )
            report.status = self._status(report.findings)
        return report

    def render_documents(self, contract: ProjectContract) -> dict[str, str]:
        return render_contract_documents(contract)

    async def documents(self, project_id: str, contract_id: str, version: int) -> dict[str, str]:
        return self.render_documents(await self._contract(project_id, contract_id, version))

    async def _contract(self, project_id: str, contract_id: str, version: int) -> ProjectContract:
        record = await self.store.get("project_contract", contract_id, str(version))
        if not isinstance(record, ProjectContract) or record.project_id != project_id:
            raise ProjectContractNotFoundError("项目合同不存在")
        return record

    def _project(self, project_id: str):
        try:
            return self.projects.get(project_id)
        except ProjectNotFoundError as error:
            raise ProjectContractNotFoundError("项目不存在") from error

    @staticmethod
    def _commit(repository: Path) -> str:
        result = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    @staticmethod
    def _replace(record, **changes):
        return type(record).model_validate({**record.model_dump(), **changes})

    @staticmethod
    def _status(findings: list[GateFinding]) -> str:
        if any(item.status == "failed" for item in findings):
            return "failed"
        if any(item.status == "manual" for item in findings):
            return "manual_review"
        return "passed"


__all__ = [
    "ProjectContractConflictError",
    "ProjectContractGateError",
    "ProjectContractNotFoundError",
    "ProjectContractService",
]
