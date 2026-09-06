from __future__ import annotations

import asyncio
import re
import shlex
import shutil
from pathlib import Path, PurePosixPath

from taskhub_v2.domain.models import ProjectDefinition
from taskhub_v2.projects.registry import ProjectRegistry


class ProjectProvisionError(RuntimeError):
    pass


class ProjectProvisioner:
    def __init__(
        self,
        registry: ProjectRegistry,
        authority_host: str,
        authority_root: str,
        repository_root: str,
    ):
        self.registry = registry
        self.authority_host = authority_host
        self.authority_root = PurePosixPath(authority_root)
        self.repository_root = Path(repository_root).expanduser().resolve()

    async def create(
        self,
        name: str,
        project_id: str,
        base_ref: str,
        test_commands: list[list[str]],
        acceptance_commands: list[list[str]] | None = None,
    ) -> ProjectDefinition:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,63}", project_id):
            raise ProjectProvisionError("项目编号只能使用小写字母、数字、短横线和下划线")
        target = (self.repository_root / project_id).resolve()
        if not target.is_relative_to(self.repository_root):
            raise ProjectProvisionError("项目路径越过托管仓库根目录")
        if target.exists():
            raise ProjectProvisionError(f"控制中心项目目录 {target} 已存在")

        remote_path = self.authority_root / f"{project_id}.git"
        remote_url = f"ssh://{self.authority_host}{remote_path}"
        create_command = (
            f"test ! -e {shlex.quote(str(remote_path))} && "
            f"git init --bare --initial-branch={shlex.quote(base_ref)} "
            f"{shlex.quote(str(remote_path))}"
        )
        await self._run(
            "ssh",
            "-o",
            "BatchMode=yes",
            self.authority_host,
            create_command,
            error_prefix="无法在权威主机创建 Git 仓库",
        )
        try:
            self.repository_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            await self._run(
                "git",
                "clone",
                remote_url,
                str(target),
                error_prefix="权威仓库已创建，但控制中心克隆失败",
            )
            await self._git(target, "symbolic-ref", "HEAD", f"refs/heads/{base_ref}")
            readme = target / "README.md"
            readme.write_text(f"# {name}\n", encoding="utf-8")
            await self._git(target, "add", "README.md")
            await self._git(
                target,
                "-c",
                "user.name=TaskHub V2",
                "-c",
                "user.email=taskhub@local",
                "commit",
                "-m",
                "Initialize project",
            )
            await self._git(target, "push", "-u", "origin", base_ref)
            project = ProjectDefinition(
                id=project_id,
                name=name,
                repository=str(target),
                authority_remote="origin",
                base_ref=base_ref,
                test_commands=test_commands,
                acceptance_commands=acceptance_commands or [],
            )
            return self.registry.add(project)
        except Exception as exc:
            if target.is_dir():
                shutil.rmtree(target)
            try:
                await self._run(
                    "ssh",
                    "-o",
                    "BatchMode=yes",
                    self.authority_host,
                    f"rm -rf -- {shlex.quote(str(remote_path))}",
                    error_prefix="回滚未完成项目失败",
                )
            except ProjectProvisionError as rollback_error:
                raise ProjectProvisionError(f"{exc}；{rollback_error}") from exc
            raise

    async def available(self) -> list[dict]:
        command = (
            f"find {shlex.quote(str(self.authority_root))} -mindepth 1 -maxdepth 1 "
            "-type d -name '*.git' -printf '%f\\n'"
        )
        output = await self._run(
            "ssh",
            "-o",
            "BatchMode=yes",
            self.authority_host,
            command,
            error_prefix="无法读取权威 Git 仓库",
        )
        projects = {project.id: project for project in self.registry.list()}
        repositories = []
        for repository in sorted(filter(None, output.splitlines())):
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.git", repository):
                continue
            project_id = self._project_id(repository.removesuffix(".git"))
            branch = await self._default_branch(repository)
            repositories.append(
                {
                    "repository": repository,
                    "name": repository.removesuffix(".git"),
                    "default_branch": branch,
                    "attached": project_id in projects,
                    "project_id": project_id,
                }
            )
        return repositories

    async def attach(
        self,
        repository: str,
        name: str,
        base_ref: str,
        test_commands: list[list[str]],
        acceptance_commands: list[list[str]] | None = None,
    ) -> ProjectDefinition:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.git", repository):
            raise ProjectProvisionError("Git 仓库名称无效")
        project_id = self._project_id(repository.removesuffix(".git"))
        target = (self.repository_root / project_id).resolve()
        if not target.is_relative_to(self.repository_root):
            raise ProjectProvisionError("项目路径越过托管仓库根目录")

        for project in self.registry.list():
            if project.id == project_id:
                return project
        if target.exists():
            raise ProjectProvisionError(f"控制中心项目目录 {target} 已存在但尚未注册")

        remote_path = self.authority_root / repository
        remote_url = f"ssh://{self.authority_host}{remote_path}"
        bare_check = (
            f"test -d {shlex.quote(str(remote_path))} && "
            f"git --git-dir={shlex.quote(str(remote_path))} "
            "rev-parse --is-bare-repository | grep -qx true"
        )
        await self._run(
            "ssh",
            "-o",
            "BatchMode=yes",
            self.authority_host,
            bare_check,
            error_prefix="所选权威 Git 仓库不存在",
        )
        try:
            self.repository_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            await self._run(
                "git",
                "clone",
                "--branch",
                base_ref,
                "--single-branch",
                remote_url,
                str(target),
                error_prefix="控制中心克隆权威仓库失败",
            )
            project = ProjectDefinition(
                id=project_id,
                name=name.strip() or repository.removesuffix(".git"),
                repository=str(target),
                authority_remote="origin",
                base_ref=base_ref,
                test_commands=test_commands,
                acceptance_commands=acceptance_commands or [],
            )
            return self.registry.add(project)
        except Exception:
            if target.is_dir():
                shutil.rmtree(target)
            raise

    async def _default_branch(self, repository: str) -> str:
        remote_path = self.authority_root / repository
        try:
            branch = await self._run(
                "ssh",
                "-o",
                "BatchMode=yes",
                self.authority_host,
                f"git --git-dir={shlex.quote(str(remote_path))} "
                "show-ref --verify --quiet refs/heads/main && printf main || "
                f"git --git-dir={shlex.quote(str(remote_path))} symbolic-ref --short HEAD",
                error_prefix="无法读取仓库默认分支",
            )
            return branch.strip() or "main"
        except ProjectProvisionError:
            return "main"

    @staticmethod
    def _project_id(name: str) -> str:
        slug = re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-_")[:64]
        if len(slug) < 2:
            raise ProjectProvisionError("Git 仓库名称无法生成有效项目编号")
        return slug

    async def _git(self, repository: Path, *arguments: str) -> str:
        return await self._run(
            "git", "-C", str(repository), *arguments, error_prefix="Git 初始化失败"
        )

    @staticmethod
    async def _run(*arguments: str, error_prefix: str) -> str:
        try:
            process = await asyncio.create_subprocess_exec(
                *arguments,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60)
        except TimeoutError as exc:
            raise ProjectProvisionError(f"{error_prefix}：操作超时") from exc
        if process.returncode:
            detail = stderr.decode(errors="replace").strip()[-500:]
            raise ProjectProvisionError(f"{error_prefix}：{detail or '命令执行失败'}")
        return stdout.decode(errors="replace")
