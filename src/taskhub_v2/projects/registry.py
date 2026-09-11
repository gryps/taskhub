from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from taskhub_v2.domain.models import ProjectDefinition


class ProjectNotFoundError(LookupError):
    pass


class ProjectConflictError(ValueError):
    pass


class ProjectRegistry:
    def __init__(self, path: str):
        self.path = Path(path)
        self._lock = threading.Lock()

    def list(self) -> list[ProjectDefinition]:
        if not self.path.is_file():
            return []
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        projects = [ProjectDefinition.model_validate(item) for item in payload.get("projects", [])]
        ids = [project.id for project in projects]
        if len(ids) != len(set(ids)):
            raise ValueError("project IDs must be unique")
        return projects

    def get(self, project_id: str) -> ProjectDefinition:
        for project in self.list():
            if project.id == project_id:
                return project
        raise ProjectNotFoundError(project_id)

    def add(self, project: ProjectDefinition) -> ProjectDefinition:
        repository = Path(project.repository).expanduser().resolve()
        self._verify_repository(repository, project.base_ref)
        normalized = project.model_copy(update={"repository": str(repository)})
        with self._lock:
            projects = self.list()
            if any(item.id == normalized.id for item in projects):
                raise ProjectConflictError(f"项目编号 {normalized.id} 已存在")
            if any(Path(item.repository).resolve() == repository for item in projects):
                raise ProjectConflictError("该 Git 仓库已经接入")
            self._write([*projects, normalized])
        return normalized

    def update(self, project: ProjectDefinition) -> ProjectDefinition:
        with self._lock:
            projects = self.list()
            if not any(item.id == project.id for item in projects):
                raise ProjectNotFoundError(project.id)
            self._write([project if item.id == project.id else item for item in projects])
        return project

    def repository_settings(self, project: ProjectDefinition) -> dict:
        repository = Path(project.repository).expanduser().resolve()
        remote_url = ""
        detail = "本地权威工作副本可用"
        ready = repository.is_dir()
        if ready:
            result = self._git(
                repository, "rev-parse", "--verify", f"{project.base_ref}^{{commit}}"
            )
            ready = result.returncode == 0
            if not ready:
                detail = f"基准分支 {project.base_ref} 不存在"
        else:
            detail = "控制中心 Git 路径不存在"
        if project.authority_remote and repository.is_dir():
            result = self._git(repository, "remote", "get-url", project.authority_remote)
            if result.returncode == 0:
                remote_url = self._redact_remote_url(result.stdout.strip())
                detail = "本地仓库与远端配置完整" if ready else detail
            else:
                ready = False
                detail = f"远端 {project.authority_remote} 未配置"
        return {
            "provider": self._provider(remote_url),
            "local_path": str(repository),
            "remote_name": project.authority_remote,
            "remote_url": remote_url,
            "base_ref": project.base_ref,
            "credential_mode": (
                "seed_ssh" if remote_url.startswith(("ssh://", "git@")) else "runtime"
            ),
            "ready": ready,
            "detail": detail,
        }

    def check_repository(self, project: ProjectDefinition) -> dict:
        settings = self.repository_settings(project)
        if not settings["ready"]:
            raise ValueError(settings["detail"])
        repository = Path(project.repository).resolve()
        commit = self._git_checked(repository, "rev-parse", f"{project.base_ref}^{{commit}}")
        if project.authority_remote:
            remote_url = self._git_checked(
                repository, "remote", "get-url", project.authority_remote
            )
            self._validate_remote_url(remote_url.strip())
            remote = self._git(
                repository,
                "ls-remote",
                "--exit-code",
                "--heads",
                project.authority_remote,
                f"refs/heads/{project.base_ref}",
                timeout=20,
            )
            if remote.returncode:
                detail = remote.stderr.strip()[-500:] or "远端分支不可访问"
                raise ValueError(f"Git 远端连接失败：{detail}")
        return {
            **settings,
            "ready": True,
            "commit": commit.strip(),
            "detail": "仓库、基准分支和远端连接均正常",
        }

    def configure_repository(
        self,
        project_id: str,
        *,
        remote_name: str,
        remote_url: str,
        base_ref: str,
    ) -> ProjectDefinition:
        project = self.get(project_id)
        repository = Path(project.repository).resolve()
        self._verify_repository(repository, base_ref)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", remote_name):
            raise ValueError("Git 远端名称无效")
        self._validate_remote_url(remote_url)
        existing = self._git(repository, "remote", "get-url", remote_name)
        action = "set-url" if existing.returncode == 0 else "add"
        changed = self._git(repository, "remote", action, remote_name, remote_url)
        if changed.returncode:
            raise ValueError(changed.stderr.strip()[-500:] or "Git 远端配置失败")
        candidate = project.model_copy(
            update={"authority_remote": remote_name, "base_ref": base_ref}
        )
        try:
            self.check_repository(candidate)
            return self.update(candidate)
        except Exception:
            if existing.returncode == 0:
                self._git(repository, "remote", "set-url", remote_name, existing.stdout.strip())
            else:
                self._git(repository, "remote", "remove", remote_name)
            raise

    def _write(self, projects: list[ProjectDefinition]) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        payload = {"projects": [item.model_dump(mode="json") for item in projects]}
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.chmod(0o600)
        os.replace(temporary, self.path)

    @staticmethod
    def _verify_repository(repository: Path, base_ref: str) -> None:
        if not repository.is_absolute() or not repository.is_dir():
            raise ValueError("控制中心 Git 路径不存在")
        checks = [
            (["rev-parse", "--git-dir"], "指定路径不是 Git 仓库"),
            (["rev-parse", "--verify", f"{base_ref}^{{commit}}"], f"基准分支 {base_ref} 不存在"),
        ]
        for arguments, message in checks:
            result = subprocess.run(
                ["git", "-C", str(repository), *arguments],
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
            if result.returncode:
                raise ValueError(message)

    @staticmethod
    def _git(repository: Path, *arguments: str, timeout: int = 15):
        environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        try:
            return subprocess.run(
                ["git", "-C", str(repository), *arguments],
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
                env=environment,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError("Git 操作超时") from exc

    @classmethod
    def _git_checked(cls, cls_repository: Path, *arguments: str) -> str:
        result = cls._git(cls_repository, *arguments)
        if result.returncode:
            raise ValueError(result.stderr.strip()[-500:] or "Git 命令执行失败")
        return result.stdout

    @staticmethod
    def _validate_remote_url(value: str) -> None:
        if not value or len(value) > 1000 or any(ord(char) < 32 for char in value):
            raise ValueError("Git 远端地址无效")
        parsed = urlsplit(value)
        if parsed.scheme:
            if parsed.scheme not in {"ssh", "http", "https", "git", "file"}:
                raise ValueError("Git 远端地址协议不受支持")
            if parsed.scheme != "file" and not parsed.hostname:
                raise ValueError("Git 远端地址缺少主机名")
            if parsed.scheme in {"http", "https"} and parsed.username:
                raise ValueError("Git 地址不能包含密码或访问令牌，请使用 Seed 运行身份")
            if parsed.password or parsed.query or parsed.fragment:
                raise ValueError("Git 地址不能包含密码、访问令牌、查询参数或片段")
            return
        if Path(value).is_absolute() or re.fullmatch(r"[^\s@:]+@[^\s:]+:.+", value):
            return
        raise ValueError("Git 远端地址应使用 SSH、HTTP(S) 或绝对本地路径")

    @staticmethod
    def _redact_remote_url(value: str) -> str:
        parsed = urlsplit(value)
        if not parsed.scheme or not parsed.username:
            return value
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        username = f"{parsed.username}@" if parsed.scheme == "ssh" else ""
        return urlunsplit((parsed.scheme, f"{username}{host}", parsed.path, "", ""))

    @staticmethod
    def _provider(remote_url: str) -> str:
        lowered = remote_url.lower()
        if "github.com" in lowered:
            return "GitHub"
        if "gitlab" in lowered:
            return "GitLab"
        if "gitee.com" in lowered:
            return "Gitee"
        return "标准 Git" if remote_url else "本地 Git"
