from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from taskhub_v2.services.onboarding import model_ready


class ProjectPreflightService:
    """Evaluate every project-level prerequisite without mutating the project."""

    def __init__(self, settings, projects, contracts, scheduler):
        self.settings = settings
        self.projects = projects
        self.contracts = contracts
        self.scheduler = scheduler

    async def run(self, project_id: str) -> dict[str, Any]:
        project = self.projects.get(project_id)
        checks = []

        try:
            repository = await asyncio.to_thread(self.projects.check_repository, project)
            checks.append(
                self._check(
                    "repository",
                    "代码仓库",
                    "passed",
                    repository.get("detail", "仓库、基准分支和远端连接均正常"),
                    target="repository",
                )
            )
        except (OSError, ValueError) as exc:
            checks.append(
                self._check(
                    "repository",
                    "代码仓库",
                    "failed",
                    str(exc),
                    remediation="修正远端地址、Seed SSH 身份或基准分支后重新检测。",
                    target="repository",
                )
            )

        models_ok, models_detail = model_ready(self.settings)
        checks.append(
            self._check(
                "models",
                "模型服务",
                (
                    "warning"
                    if models_ok and self.settings.provider == "deterministic"
                    else "passed"
                    if models_ok
                    else "failed"
                ),
                models_detail,
                remediation=(
                    "完成模型认证、五类角色主路由和连接测试。" if not models_ok else ""
                ),
                target="model-services",
                blocking=not (models_ok and self.settings.provider == "deterministic"),
            )
        )

        nodes = await self.scheduler.status()
        online = [item for item in nodes if item.get("status") == "ok"]
        git_nodes = [item for item in online if item.get("capabilities", {}).get("git")]
        checks.append(
            self._check(
                "execution",
                "Seed 执行能力",
                "passed" if git_nodes else "failed",
                (
                    f"{len(git_nodes)} 个本机执行节点在线并具备 Git 能力"
                    if git_nodes
                    else "没有在线且具备 Git 能力的本机执行节点"
                ),
                remediation="启动 Seed 本机节点，并修复其 Git 与工作区写入能力。",
                target="nodes",
            )
        )

        current_contract = await self.contracts.current(project_id)
        active_contract = (
            current_contract if current_contract and current_contract.status == "active" else None
        )
        if self.settings.production_orchestration_enabled:
            checks.append(
                self._check(
                    "contract",
                    "项目契约",
                    "passed" if active_contract else "failed",
                    (
                        f"项目契约 v{active_contract.version} 已生效"
                        if active_contract
                        else "项目契约尚未批准生效"
                    ),
                    remediation="生成或核对契约后使其生效。",
                    target="project-contract",
                )
            )
        else:
            checks.append(
                self._check(
                    "contract",
                    "项目契约",
                    "passed",
                    "当前为基础流程模式，不要求项目契约。",
                    target="project-contract",
                    blocking=False,
                )
            )

        contract_commands = active_contract.commands.gate_commands() if active_contract else []
        command_count = len(contract_commands or project.test_commands)
        checks.append(
            self._check(
                "quality_commands",
                "质量命令",
                "passed" if command_count else "failed",
                (
                    f"已配置 {command_count} 条自动质量命令"
                    if command_count
                    else "未配置可执行的测试或质量命令"
                ),
                remediation="在项目接入信息或项目契约中配置至少一条质量命令。",
                target=(
                    "project-contract"
                    if self.settings.production_orchestration_enabled
                    else "repository"
                ),
            )
        )

        missing_capabilities = sorted(
            capability
            for capability in project.acceptance_capabilities
            if not any(
                item.get("capabilities", {}).get(capability) for item in online
            )
        )
        checks.append(
            self._check(
                "acceptance",
                "验收依赖",
                "failed" if missing_capabilities else "passed",
                (
                    "缺少验收能力：" + "、".join(missing_capabilities)
                    if missing_capabilities
                    else "项目声明的验收能力均可用"
                    if project.acceptance_capabilities
                    else "项目未声明额外验收能力"
                ),
                remediation="启用具备所需能力的 Seed 本机节点。",
                target="test-environment",
            )
        )

        blocking_failures = [
            item for item in checks if item["blocking"] and item["status"] == "failed"
        ]
        return {
            "project_id": project_id,
            "checked_at": datetime.now(UTC).isoformat(),
            "ready": not blocking_failures,
            "summary": {
                "passed": sum(item["status"] == "passed" for item in checks),
                "warnings": sum(item["status"] == "warning" for item in checks),
                "failed": len(blocking_failures),
                "total": len(checks),
            },
            "checks": checks,
        }

    @staticmethod
    def _check(
        check_id: str,
        title: str,
        status: str,
        detail: str,
        *,
        remediation: str = "",
        target: str,
        blocking: bool = True,
    ) -> dict[str, Any]:
        return {
            "id": check_id,
            "title": title,
            "status": status,
            "blocking": blocking,
            "detail": detail,
            "remediation": remediation if status == "failed" else "",
            "target": target,
        }
