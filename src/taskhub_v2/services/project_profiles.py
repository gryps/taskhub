from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from taskhub_v2.domain.project_contract import (
    ArtifactContract,
    ContractCommands,
    MigrationContract,
    ModuleContract,
)


@dataclass(frozen=True)
class ProjectProfile:
    profile_id: str
    name: str
    summary: str
    languages: tuple[str, ...]
    frameworks: tuple[str, ...]
    directories: tuple[str, ...]
    modules: tuple[ModuleContract, ...]
    commands: ContractCommands
    migrations: MigrationContract
    artifacts: ArtifactContract
    documentation_files: tuple[str, ...]

    def view(self) -> dict:
        return {
            "profile_id": self.profile_id,
            "name": self.name,
            "summary": self.summary,
            "languages": list(self.languages),
            "frameworks": list(self.frameworks),
        }


def _python_commands() -> ContractCommands:
    return ContractCommands(
        install=[["python3", "-m", "pip", "install", "-e", ".[dev]"]],
        format=[["python3", "-m", "ruff", "format", "--check", "."]],
        lint=[["python3", "-m", "ruff", "check", "."]],
        type_check=[["python3", "-m", "mypy", "src"]],
        test=[["python3", "-m", "pytest", "-q"]],
        architecture=[["python3", "-m", "pytest", "tests/architecture", "-q"]],
        integration=[["python3", "-m", "pytest", "tests/integration", "-q"]],
        build=[["python3", "-m", "build"]],
        security=[["python3", "-m", "pip_audit"]],
    )


def _node_commands() -> ContractCommands:
    return ContractCommands(
        install=[["npm", "ci"]],
        format=[["npm", "run", "format:check"]],
        lint=[["npm", "run", "lint"]],
        type_check=[["npm", "run", "typecheck"]],
        test=[["npm", "test", "--", "--run"]],
        architecture=[["npm", "run", "test:architecture"]],
        integration=[["npm", "run", "test:integration"]],
        build=[["npm", "run", "build"]],
        acceptance=[["npm", "run", "test:e2e"]],
        security=[["npm", "audit", "--audit-level=high"]],
    )


def _profile(
    profile_id: str,
    name: str,
    summary: str,
    modules: list[ModuleContract],
    *,
    node: bool = False,
    fullstack: bool = False,
) -> ProjectProfile:
    commands = _node_commands() if node else _python_commands()
    if fullstack:
        python = _python_commands()
        commands = ContractCommands(
            **{
                field: [*getattr(python, field), *getattr(commands, field)]
                for field in ContractCommands.model_fields
            }
        )
    directories = tuple(sorted({path.split("/")[0] for module in modules for path in module.paths}))
    return ProjectProfile(
        profile_id=profile_id,
        name=name,
        summary=summary,
        languages=("Python", "TypeScript")
        if fullstack
        else (("TypeScript",) if node else ("Python",)),
        frameworks=(),
        directories=directories,
        modules=tuple(modules),
        commands=commands,
        migrations=MigrationContract(paths=["migrations"] if not node else []),
        artifacts=ArtifactContract(
            required_files=[
                "README.md",
                ".env.example",
                "docs/architecture.md",
                ".taskhub/project.yaml",
                ".taskhub/architecture.yaml",
                ".taskhub/acceptance.yaml",
            ],
            required_artifacts=["dist/**"] if node else ["dist/*"],
        ),
        documentation_files=("README.md", "docs/architecture.md", "docs/adr/README.md"),
    )


def profile_catalog() -> dict[str, ProjectProfile]:
    domain = ModuleContract(name="domain", paths=["src/domain/**"])
    return {
        "backend-api": _profile(
            "backend-api",
            "后端 API",
            "分层 API 服务与可回滚数据迁移",
            [
                domain,
                ModuleContract(
                    name="application", paths=["src/application/**"], may_import=["domain"]
                ),
                ModuleContract(
                    name="api", paths=["src/api/**"], may_import=["application", "domain"]
                ),
                ModuleContract(
                    name="infrastructure",
                    paths=["src/infrastructure/**"],
                    may_import=["domain"],
                    allow_data_access=True,
                ),
            ],
        ),
        "python-service": _profile(
            "python-service",
            "Python 服务",
            "可测试的 Python 应用、领域与基础设施分层",
            [
                domain,
                ModuleContract(
                    name="application", paths=["src/application/**"], may_import=["domain"]
                ),
                ModuleContract(
                    name="infrastructure",
                    paths=["src/infrastructure/**"],
                    may_import=["domain"],
                    allow_data_access=True,
                ),
            ],
        ),
        "worker-service": _profile(
            "worker-service",
            "后台工作服务",
            "队列入口、领域处理与外部适配器隔离",
            [
                domain,
                ModuleContract(
                    name="worker", paths=["src/worker/**"], may_import=["domain", "adapters"]
                ),
                ModuleContract(
                    name="adapters",
                    paths=["src/adapters/**"],
                    may_import=["domain"],
                    allow_data_access=True,
                ),
            ],
        ),
        "frontend-spa": _profile(
            "frontend-spa",
            "前端 SPA",
            "组件、功能和服务访问边界清晰的单页应用",
            [
                ModuleContract(name="ui", paths=["src/ui/**"]),
                ModuleContract(name="services", paths=["src/services/**"]),
                ModuleContract(
                    name="features", paths=["src/features/**"], may_import=["ui", "services"]
                ),
                ModuleContract(
                    name="app", paths=["src/app/**"], may_import=["features", "ui", "services"]
                ),
            ],
            node=True,
        ),
        "fullstack-web": _profile(
            "fullstack-web",
            "全栈 Web",
            "前后端分区、领域内聚和明确接口边界",
            [
                ModuleContract(name="frontend", paths=["frontend/src/**"]),
                ModuleContract(name="domain", paths=["backend/src/domain/**"]),
                ModuleContract(name="api", paths=["backend/src/api/**"], may_import=["domain"]),
                ModuleContract(
                    name="infrastructure",
                    paths=["backend/src/infrastructure/**"],
                    may_import=["domain"],
                    allow_data_access=True,
                ),
            ],
            node=True,
            fullstack=True,
        ),
    }


def detect_profile(repository: Path) -> str:
    package = repository / "package.json"
    pyproject = repository / "pyproject.toml"
    if (repository / "frontend").is_dir() and (repository / "backend").is_dir():
        return "fullstack-web"
    if package.is_file() and not pyproject.is_file():
        return "frontend-spa"
    if any((repository / name).exists() for name in ("worker.py", "celery.py", "src/worker")):
        return "worker-service"
    if any((repository / name).exists() for name in ("openapi.yaml", "openapi.json", "src/api")):
        return "backend-api"
    if package.is_file() and pyproject.is_file():
        try:
            payload = json.loads(package.read_text(encoding="utf-8"))
            if payload.get("scripts", {}).get("build"):
                return "fullstack-web"
        except (OSError, ValueError):
            pass
    return "python-service"
