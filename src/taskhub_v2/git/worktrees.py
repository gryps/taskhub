import asyncio
import re
import subprocess
from pathlib import Path

from taskhub_v2.domain.models import ProjectDefinition, Workspace


class WorkspaceError(RuntimeError):
    pass


class GitWorkspaceManager:
    def __init__(self, root: str):
        self.root = Path(root).resolve()

    async def prepare(self, project: ProjectDefinition, run_id: str) -> Workspace:
        return await asyncio.to_thread(self._prepare, project, run_id)

    def _prepare(self, project: ProjectDefinition, run_id: str) -> Workspace:
        repository = Path(project.repository).resolve()
        self._verify_repository(repository)
        if project.authority_remote:
            self._sync_authority(repository, project.authority_remote, project.base_ref)
        safe_run_id = re.sub(r"[^a-zA-Z0-9-]", "-", run_id)
        target = (self.root / project.id / safe_run_id).resolve()
        if not target.is_relative_to(self.root):
            raise WorkspaceError("workspace path escaped configured root")
        branch = f"taskhub/{safe_run_id}"
        base_commit = self._git(repository, "rev-parse", project.base_ref).strip()
        if target.exists():
            current = self._git(target, "rev-parse", "HEAD").strip()
            branch_base = self._original_base(target, current, project.base_ref, run_id)
            return Workspace(
                project_id=project.id,
                path=str(target),
                branch=branch,
                base_commit=branch_base,
            )
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        branch_exists = subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "show-ref",
                "--verify",
                "--quiet",
                f"refs/heads/{branch}",
            ],
            check=False,
        ).returncode == 0
        if branch_exists:
            self._git(repository, "worktree", "add", str(target), branch)
        else:
            self._git(repository, "worktree", "add", "-b", branch, str(target), base_commit)
        return Workspace(
            project_id=project.id,
            path=str(target),
            branch=branch,
            base_commit=base_commit,
        )

    def _original_base(
        self, target: Path, current: str, base_ref: str, run_id: str
    ) -> str:
        first_run_commit = self._git(
            target,
            "log",
            "--reverse",
            "--format=%H",
            "--fixed-strings",
            f"--grep=TaskHub-Run: {run_id}",
            current,
        ).splitlines()
        if first_run_commit:
            return self._git(target, "rev-parse", f"{first_run_commit[0]}^").strip()
        return self._git(target, "merge-base", current, base_ref).strip()

    def _sync_authority(self, repository: Path, remote: str, base_ref: str) -> None:
        if self._git(repository, "status", "--porcelain").strip():
            raise WorkspaceError("managed authority checkout has uncommitted changes")
        self._git(repository, "fetch", remote, base_ref)
        remote_ref = f"{remote}/{base_ref}"
        local_commit = self._git(repository, "rev-parse", base_ref).strip()
        remote_commit = self._git(repository, "rev-parse", remote_ref).strip()
        if local_commit != remote_commit:
            self._git(repository, "merge", "--ff-only", remote_ref)

    @staticmethod
    def _verify_repository(repository: Path) -> None:
        if not repository.is_dir():
            raise WorkspaceError("registered repository does not exist")
        result = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "--git-dir"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise WorkspaceError("registered repository is not a Git repository")

    @staticmethod
    def _git(repository: Path, *arguments: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode:
            raise WorkspaceError(completed.stderr.strip()[:500] or "Git command failed")
        return completed.stdout
