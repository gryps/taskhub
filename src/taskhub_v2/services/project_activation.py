from __future__ import annotations

from pathlib import Path
from typing import Any

from taskhub_v2.domain.project_contract import ProjectContractStatus
from taskhub_v2.services.command_lines import format_command_lines


class ProjectActivationService:
    """Compose the server-owned project activation path from existing authorities."""

    def __init__(self, projects, contracts, preflight, *, contracts_required: bool):
        self.projects = projects
        self.contracts = contracts
        self.preflight = preflight
        self.contracts_required = contracts_required

    async def status(self, project_id: str) -> dict[str, Any]:
        project = self.projects.get(project_id)
        report = await self.preflight.run(project_id)
        checks = {item["id"]: item for item in report["checks"]}
        contract = await self.contracts.current(project_id) if self.contracts_required else None
        commands = contract.commands.gate_commands() if contract else project.test_commands
        contract_complete, contract_detail = self._contract_state(contract)
        acceptance_required = self._acceptance_required(project)
        steps = self._steps(
            checks, report, commands, contract_complete, contract_detail, acceptance_required
        )
        next_step = next((item for item in steps if not item["complete"]), None)
        return {
            "project_id": project_id,
            "project_name": project.name or project.id,
            "ready": report["ready"],
            "message": (
                "项目已激活，可以开始首个开发需求"
                if report["ready"]
                else "完成项目级约束后再启动开发"
            ),
            "completed": sum(item["complete"] for item in steps),
            "total": len(steps),
            "next_target": next_step["target"] if next_step else "requirement",
            "quality_commands": format_command_lines(commands),
            "contract": self._contract_view(contract),
            "acceptance_required": acceptance_required,
            "steps": steps,
            "preflight": report,
        }

    def _steps(
        self,
        checks,
        report,
        commands,
        contract_complete,
        contract_detail,
        acceptance_required,
    ) -> list[dict[str, Any]]:
        acceptance = checks["acceptance"]
        return [
            self._step(
                "repository",
                "接入代码仓库",
                checks["repository"]["status"] == "passed",
                checks["repository"]["detail"],
                "repository",
            ),
            self._step(
                "contract",
                "核对并激活项目契约",
                contract_complete,
                contract_detail,
                "project-contract",
            ),
            self._step(
                "quality_commands",
                "确认质量命令",
                bool(commands),
                f"已写入 {len(commands)} 条可执行质量命令" if commands else "尚未写入项目质量命令",
                "project-contract-quality" if self.contracts_required else "repository",
            ),
            self._step(
                "acceptance",
                "准备项目验收资源" if acceptance_required else "确认验收范围",
                acceptance["status"] != "failed",
                acceptance["detail"],
                acceptance["target"],
                optional=not acceptance_required,
            ),
            self._step(
                "preflight",
                "完成项目预检",
                report["ready"],
                "全部启动条件已通过，可以填写首个开发需求"
                if report["ready"]
                else f"仍有 {report['summary']['failed']} 项启动条件阻塞",
                "project-preflight",
            ),
        ]

    def _contract_state(self, contract) -> tuple[bool, str]:
        if not self.contracts_required:
            return True, "当前模式不要求项目契约"
        if not contract:
            return False, "尚未生成项目契约"
        if contract.status == ProjectContractStatus.ACTIVE:
            return True, f"项目契约 v{contract.version} 已生效"
        return False, f"项目契约 v{contract.version} 为{self._contract_status(contract.status)}"

    @staticmethod
    def _contract_view(contract) -> dict[str, Any] | None:
        if not contract:
            return None
        return {
            "contract_id": contract.contract_id,
            "version": contract.version,
            "status": contract.status,
            "editable": contract.status
            in {ProjectContractStatus.DRAFT, ProjectContractStatus.IN_REVIEW},
        }

    @staticmethod
    def _acceptance_required(project) -> bool:
        return bool(
            project.acceptance_capabilities
            or project.windows_acceptance_node_ids
            or project.windows_test_suite
            or (Path(project.repository) / ".taskhub" / "acceptance.yaml").is_file()
        )

    @staticmethod
    def _step(
        step_id: str,
        title: str,
        complete: bool,
        detail: str,
        target: str,
        *,
        optional: bool = False,
    ) -> dict[str, Any]:
        return {
            "id": step_id,
            "title": title,
            "complete": complete,
            "detail": detail,
            "target": target,
            "optional": optional,
        }

    @staticmethod
    def _contract_status(status: ProjectContractStatus) -> str:
        return {
            ProjectContractStatus.DRAFT: "草稿",
            ProjectContractStatus.IN_REVIEW: "待批准",
            ProjectContractStatus.ACTIVE: "已生效",
            ProjectContractStatus.SUPERSEDED: "已废弃",
            ProjectContractStatus.REJECTED: "已拒绝",
        }[status]
