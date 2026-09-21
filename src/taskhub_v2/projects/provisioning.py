from __future__ import annotations

import asyncio
import os
import re
import shlex
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

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
        authority_port: int = 22,
        private_key: str = "",
        private_key_file: str = "",
        known_hosts_file: str = "",
    ):
        self.registry = registry
        self.authority_host = authority_host
        self.authority_user, self.authority_hostname = authority_host.rsplit("@", 1)
        self.authority_port = authority_port
        self.authority_root = PurePosixPath(authority_root)
        self.repository_root = Path(repository_root).expanduser().resolve()
        self.private_key = private_key.strip()
        self.private_key_file = Path(private_key_file).resolve() if private_key_file else None
        self.known_hosts_file = Path(
            known_hosts_file or self.repository_root.parent / "ssh/known_hosts"
        ).expanduser().resolve()

    async def create(
        self,
        name: str,
        project_id: str,
        remote_url: str,
        local_path: str,
        base_ref: str,
        test_commands: list[list[str]],
        acceptance_commands: list[list[str]] | None = None,
        acceptance_capabilities: set[str] | None = None,
    ) -> ProjectDefinition:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,63}", project_id):
            raise ProjectProvisionError("项目编号只能使用小写字母、数字、短横线和下划线")
        target = self._local_target(local_path)
        if target.exists():
            raise ProjectProvisionError(f"控制中心项目目录 {target} 已存在")

        remote_path = self._authority_repository(remote_url)
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
            await self._git(target, "config", "core.sshCommand", self._ssh_command())
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
                acceptance_capabilities=acceptance_capabilities or set(),
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
                    "remote_url": self._remote_url(self.authority_root / repository),
                    "local_path": str((self.repository_root / project_id).resolve()),
                }
            )
        return repositories

    def defaults(self) -> dict[str, str]:
        return {
            "authority_url_prefix": self._remote_url(self.authority_root).rstrip("/"),
            "managed_repository_root": str(self.repository_root),
            "authority_service": (
                f"{self.authority_host}:{self.authority_port} · {self.authority_root}"
            ),
        }

    async def test_connection(self) -> dict[str, str | bool]:
        output = await self._run(
            "ssh",
            "-o",
            "BatchMode=yes",
            self.authority_host,
            f"test -d {shlex.quote(str(self.authority_root))} && "
            "git --version && printf '\\nTASKHUB_GIT_READY'",
            error_prefix="Git 仓库服务连接失败",
        )
        if "TASKHUB_GIT_READY" not in output:
            raise ProjectProvisionError("Git 仓库服务未返回就绪标记")
        return {
            "available": True,
            "detail": f"连接成功 · {self.authority_host}:{self.authority_port}",
        }

    async def attach(
        self,
        name: str,
        remote_url: str,
        local_path: str,
        base_ref: str,
        test_commands: list[list[str]],
        acceptance_commands: list[list[str]] | None = None,
        acceptance_capabilities: set[str] | None = None,
    ) -> ProjectDefinition:
        remote_path = self._authority_repository(remote_url)
        repository = remote_path.name
        project_id = self._project_id(repository.removesuffix(".git"))
        target = self._local_target(local_path)

        for project in self.registry.list():
            if project.id == project_id:
                return project
        if target.exists():
            raise ProjectProvisionError(f"控制中心项目目录 {target} 已存在但尚未注册")

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
            await self._git(target, "config", "core.sshCommand", self._ssh_command())
            project = ProjectDefinition(
                id=project_id,
                name=name.strip() or repository.removesuffix(".git"),
                repository=str(target),
                authority_remote="origin",
                base_ref=base_ref,
                test_commands=test_commands,
                acceptance_commands=acceptance_commands or [],
                acceptance_capabilities=acceptance_capabilities or set(),
            )
            return self.registry.add(project)
        except Exception:
            if target.is_dir():
                shutil.rmtree(target)
            raise

    def _authority_repository(self, remote_url: str) -> PurePosixPath:
        parsed = urlsplit(remote_url.strip())
        if (
            parsed.scheme != "ssh"
            or parsed.username != self.authority_user
            or parsed.hostname != self.authority_hostname
            or (parsed.port or 22) != self.authority_port
        ):
            raise ProjectProvisionError(
                f"Git 远端地址必须位于 {self._remote_url(self.authority_root)}"
            )
        if parsed.password or parsed.query or parsed.fragment:
            raise ProjectProvisionError("Git 远端地址不能包含密码、查询参数或片段")
        remote_path = PurePosixPath(parsed.path)
        if remote_path.parent != self.authority_root or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.git", remote_path.name
        ):
            raise ProjectProvisionError("Git 远端地址必须指向权威仓库根目录下的 .git 仓库")
        return remote_path

    def _local_target(self, local_path: str) -> Path:
        if not local_path.strip():
            raise ProjectProvisionError("必须填写控制中心工作副本位置")
        target = Path(local_path).expanduser().resolve()
        if target == self.repository_root or not target.is_relative_to(self.repository_root):
            raise ProjectProvisionError(
                f"控制中心工作副本必须位于托管目录 {self.repository_root} 内"
            )
        return target

    def _remote_url(self, remote_path: PurePosixPath) -> str:
        port = f":{self.authority_port}" if self.authority_port != 22 else ""
        return f"ssh://{self.authority_host}{port}{remote_path}"

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

    async def _run(self, *arguments: str, error_prefix: str) -> str:
        key_path = None
        try:
            self.known_hosts_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            ssh_options = self._ssh_options()
            if self.private_key:
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", prefix="taskhub-git-", delete=False
                ) as key:
                    key.write(self.private_key + "\n")
                key_path = Path(key.name)
                key_path.chmod(0o600)
                ssh_options[0:0] = ["-i", str(key_path), "-o", "IdentitiesOnly=yes"]
            command = list(arguments)
            if command[0] == "ssh":
                command[1:1] = ssh_options
            environment = os.environ.copy()
            environment["GIT_SSH_COMMAND"] = self._ssh_command(ssh_options)
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=environment,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60)
        except FileNotFoundError as exc:
            raise ProjectProvisionError(
                f"{error_prefix}：运行环境缺少命令 {arguments[0]}"
            ) from exc
        except TimeoutError as exc:
            raise ProjectProvisionError(f"{error_prefix}：操作超时") from exc
        finally:
            if key_path:
                key_path.unlink(missing_ok=True)
        if process.returncode:
            detail = stderr.decode(errors="replace").strip()[-500:]
            raise ProjectProvisionError(f"{error_prefix}：{detail or '命令执行失败'}")
        return stdout.decode(errors="replace")

    def _ssh_options(self) -> list[str]:
        options = [
            "-p", str(self.authority_port),
            "-o", f"UserKnownHostsFile={self.known_hosts_file}",
            "-o", "StrictHostKeyChecking=accept-new",
        ]
        if self.private_key_file:
            options[0:0] = [
                "-i", str(self.private_key_file), "-o", "IdentitiesOnly=yes",
            ]
        return options

    def _ssh_command(self, options: list[str] | None = None) -> str:
        selected = self._ssh_options() if options is None else options
        return " ".join(["ssh", *(shlex.quote(item) for item in selected)])
