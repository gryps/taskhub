import asyncio
from pathlib import Path

from taskhub_v2.domain.models import ExecutionResult, PublicationResult
from taskhub_v2.execution import LocalTestScheduler
from taskhub_v2.projects import ProjectRegistry


class PublicationError(RuntimeError):
    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


class LocalPublisher:
    async def publish(
        self, run_id: str, project_id: str, implementation: ExecutionResult
    ) -> PublicationResult:
        return PublicationResult(
            project_id=project_id,
            authority_ref="local",
            previous_commit="local",
            published_commit=implementation.commit or "local",
            branch=f"taskhub/{run_id}",
        )


class GitPublisher:
    def __init__(self, projects: ProjectRegistry, workspace_root: str, test_scheduler=None):
        self.projects = projects
        self.workspace_root = Path(workspace_root).resolve()
        self.test_scheduler = test_scheduler or LocalTestScheduler()

    async def publish(
        self, run_id: str, project_id: str, implementation: ExecutionResult
    ) -> PublicationResult:
        project = self.projects.get(project_id)
        workspace = implementation.workspace
        if not workspace or not implementation.commit:
            raise PublicationError("missing_evidence", "implementation has no Git workspace")
        repository = Path(project.repository).resolve()
        worktree = Path(workspace.path).resolve()
        expected = (self.workspace_root / project_id / run_id).resolve()
        if worktree != expected or not worktree.is_relative_to(self.workspace_root):
            raise PublicationError("invalid_workspace", "workspace is outside the registered root")
        if workspace.project_id != project_id:
            raise PublicationError("invalid_workspace", "workspace project does not match run")

        branch = (await self._git(worktree, "branch", "--show-current")).strip()
        if branch != workspace.branch:
            raise PublicationError("invalid_branch", "worktree is not on its run branch")
        head = (await self._git(worktree, "rev-parse", "HEAD")).strip()
        if head != implementation.commit:
            reviewed_patch = await self._patch_id(worktree, implementation.commit)
            current_patch = await self._patch_id(worktree, head)
            if reviewed_patch != current_patch:
                raise PublicationError(
                    "changed_after_review", "run branch changed after supervision"
                )
        if (await self._git(repository, "status", "--porcelain")).strip():
            raise PublicationError("authority_dirty", "authority worktree has uncommitted changes")
        active = (await self._git(repository, "branch", "--show-current")).strip()
        if active != project.base_ref:
            raise PublicationError(
                "authority_ref_not_checked_out",
                f"authority worktree must be on {project.base_ref}",
            )

        remote_commit = ""
        if project.authority_remote:
            await self._git(repository, "fetch", project.authority_remote, project.base_ref)
            remote_ref = f"{project.authority_remote}/{project.base_ref}"
            remote_commit = (await self._git(repository, "rev-parse", remote_ref)).strip()
            local_commit = (await self._git(repository, "rev-parse", project.base_ref)).strip()
            if local_commit != remote_commit:
                try:
                    await self._git(repository, "merge", "--ff-only", remote_ref)
                except PublicationError as exc:
                    raise PublicationError(
                        "authority_diverged",
                        "权威仓库与控制中心基线已经分叉，必须人工处理",
                    ) from exc

        previous = (await self._git(repository, "rev-parse", project.base_ref)).strip()
        rebased = previous != workspace.base_commit
        if rebased:
            try:
                await self._git(
                    worktree,
                    "-c",
                    "user.name=TaskHub V2",
                    "-c",
                    "user.email=taskhub@local",
                    "rebase",
                    project.base_ref,
                )
            except PublicationError as exc:
                await self._git(worktree, "rebase", "--abort", allow_failure=True)
                raise PublicationError(
                    "merge_conflict", "authority advanced and the run branch could not be rebased"
                ) from exc

        scheduled = await self.test_scheduler.run(
            f"{run_id}-publish",
            run_id,
            project.test_commands,
            project.test_timeout_seconds,
            str(worktree),
        )
        tests = scheduled.tests
        failed = [test for test in tests if test.exit_code]
        if failed:
            raise PublicationError("publication_tests_failed", failed[0].output_tail)
        published = (await self._git(worktree, "rev-parse", "HEAD")).strip()
        if project.authority_remote:
            lease = f"refs/heads/{project.base_ref}:{remote_commit}"
            try:
                await self._git(
                    worktree,
                    "push",
                    f"--force-with-lease={lease}",
                    project.authority_remote,
                    f"{published}:refs/heads/{project.base_ref}",
                )
            except PublicationError as exc:
                raise PublicationError(
                    "authority_push_rejected",
                    "权威仓库在发布期间发生变化，请重试发布",
                ) from exc
        try:
            await self._git(repository, "merge", "--ff-only", published)
        except PublicationError as exc:
            raise PublicationError(
                "authority_advanced", "authority changed during publication; retry is safe"
            ) from exc
        return PublicationResult(
            project_id=project_id,
            authority_ref=(
                f"{project.authority_remote}/{project.base_ref}"
                if project.authority_remote
                else project.base_ref
            ),
            previous_commit=previous,
            published_commit=published,
            branch=branch,
            rebased=rebased,
            tests=tests,
            execution_node=scheduled.node_id,
        )

    @staticmethod
    async def _patch_id(workdir: Path, commit: str) -> str:
        show = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(workdir),
            "show",
            "--pretty=format:",
            "--binary",
            commit,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        patch, show_error = await show.communicate()
        if show.returncode:
            raise PublicationError("invalid_commit", show_error.decode(errors="replace")[-1000:])
        patch_id = await asyncio.create_subprocess_exec(
            "git",
            "patch-id",
            "--stable",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await patch_id.communicate(patch)
        if patch_id.returncode or not stdout.strip():
            raise PublicationError(
                "invalid_commit", stderr.decode(errors="replace")[-1000:] or "empty patch"
            )
        return stdout.decode().split()[0]

    @staticmethod
    async def _git(workdir: Path, *arguments: str, allow_failure: bool = False) -> str:
        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(workdir),
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode and not allow_failure:
            raise PublicationError(
                "git_failed", stderr.decode(errors="replace")[-1000:] or "Git command failed"
            )
        return stdout.decode(errors="replace")
