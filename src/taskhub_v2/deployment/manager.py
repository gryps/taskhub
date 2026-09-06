from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from taskhub_v2.config import Settings
from taskhub_v2.domain.models import ProjectDefinition, PublicationResult


class DeploymentError(RuntimeError):
    pass


ACTIVE_STATUSES = {"queued", "validating", "testing", "deploying", "restarting"}


class DeploymentManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.state_file = Path(settings.self_deploy_state_file).expanduser().resolve()

    def eligible(self, project_id: str) -> bool:
        return bool(
            self.settings.self_deploy_enabled
            and self.settings.self_deploy_project_id == project_id
            and self.settings.self_deploy_target
        )

    def status(self) -> dict:
        if not self.state_file.is_file():
            return {"status": "idle"}
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"status": "unknown", "detail": "部署状态文件不可读"}

    async def start(self, project: ProjectDefinition, publication: PublicationResult) -> dict:
        if not self.eligible(project.id):
            raise DeploymentError("该项目没有配置自动部署目标")
        if not re.fullmatch(r"[a-f0-9]{40}", publication.published_commit):
            raise DeploymentError("发布提交号无效")
        current = self.status()
        if current.get("status") in ACTIVE_STATUSES:
            raise DeploymentError("已有部署任务正在执行")
        if not project.test_commands:
            raise DeploymentError("项目没有配置强制测试命令")

        deployment_id = f"deploy-{uuid4().hex[:12]}"
        queued = {
            "deployment_id": deployment_id,
            "project_id": project.id,
            "commit": publication.published_commit,
            "status": "queued",
            "detail": "等待独立部署执行器启动",
            "updated_at": datetime.now(UTC).isoformat(),
        }
        self._write_state(queued)
        command = [
            "systemd-run",
            "--user",
            "--collect",
            f"--unit=taskhub-deploy-{uuid4().hex[:10]}",
            f"--setenv=PYTHONPATH={Path(self.settings.provider_workdir).resolve() / 'src'}",
            sys.executable,
            "-m",
            "taskhub_v2.deployment.runner",
            "--deployment-id",
            deployment_id,
            "--project-id",
            project.id,
            "--repository",
            project.repository,
            "--remote",
            project.authority_remote or "origin",
            "--branch",
            project.base_ref,
            "--commit",
            publication.published_commit,
            "--tests-json",
            json.dumps(project.test_commands),
            "--target",
            self.settings.self_deploy_target,
            "--service",
            self.settings.self_deploy_service,
            "--state-file",
            str(self.state_file),
            "--health-url",
            self.settings.self_deploy_health_url,
        ]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode:
            detail = stderr.decode(errors="replace").strip()[-500:]
            failed = {
                **queued,
                "status": "failed",
                "detail": detail or "部署执行器启动失败",
            }
            self._write_state(failed)
            raise DeploymentError(failed["detail"])
        return {**queued, "launcher": stdout.decode(errors="replace").strip()}

    def _write_state(self, payload: dict) -> None:
        self.state_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="deployment.", dir=self.state_file.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(temporary, self.state_file)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
