from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path

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
