import asyncio
import shutil
from pathlib import Path

from taskhub_v2.artifacts import ArtifactStore
from taskhub_v2.domain.models import ExecutionResult, ModelRun, Plan
from taskhub_v2.execution import LocalTestScheduler
from taskhub_v2.git import GitWorkspaceManager
from taskhub_v2.projects import ProjectRegistry
from taskhub_v2.workers.change_detection import (
    artifact_name,
    feedback_key,
    is_generated,
    no_change_feedback,
    worktree_fingerprint,
)
from taskhub_v2.workers.coding_router import CodexCodingRouter
from taskhub_v2.workers.test_diagnostics import classify_test_failure, failed_test_diagnostics

FORBIDDEN_FILES = {".env", ".env.local", "auth.json", "credentials.json"}

class WorkerExecutionError(RuntimeError):
    def __init__(self, reason: str, detail: str, model_results=None, diagnostics=None):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail
        self.model_results = model_results or []
        self.diagnostics = diagnostics or []


def raise_for_failed_tests(tests, *, model_results=None) -> None:
    if not any(test.exit_code for test in tests):
        return
    detail, diagnostics = failed_test_diagnostics(tests)
    raise WorkerExecutionError(
        classify_test_failure(tests),
        detail,
        model_results=model_results,
        diagnostics=diagnostics,
    )


class GitCodingWorker:
    def __init__(
        self,
        projects: ProjectRegistry,
        workspaces: GitWorkspaceManager,
        coder: CodexCodingRouter,
        artifacts: ArtifactStore,
        test_scheduler=None,
    ):
        self.projects = projects
        self.workspaces = workspaces
        self.coder = coder
        self.artifacts = artifacts
        self.test_scheduler = test_scheduler or LocalTestScheduler()

    async def execute(
        self,
        run_id: str,
        project_id: str,
        requirement: str,
        plan: Plan,
        revision: int = 0,
        feedback: str = "",
        base_commit: str = "",
        task_context: dict | None = None,
    ) -> ExecutionResult:
        project = self.projects.get(project_id)
        workspace = await self.workspaces.prepare(project, run_id, base_commit)
        await self._cleanup_generated_untracked(workspace.path)
        existing = await self._existing_result(
            workspace.path, workspace.base_commit, revision, feedback
        )
        if existing:
            existing.workspace = workspace
            scheduled = await self.test_scheduler.run(
                f"{run_id}-r{revision}",
                run_id,
                project.test_commands,
                project.test_timeout_seconds,
                workspace.path,
            )
            existing.tests = scheduled.tests
            existing.execution_node = scheduled.node_id
            raise_for_failed_tests(existing.tests)
            existing.artifacts = [
                self.artifacts.write_text(
                    run_id, artifact_name(revision), "git_diff", existing.evidence
                )
            ]
            return existing

        # Retest uncommitted edits once before spending another model call.
        retest_feedback = ""
        if revision:
            recovered, retest_feedback = await self._recover_uncommitted_if_valid(
                run_id, revision, workspace, project, feedback
            )
            if recovered:
                return recovered

        model_results = []
        coding_feedback = feedback
        if retest_feedback:
            coding_feedback = f"{feedback}\n\nLatest quality retest failed:\n{retest_feedback}"
            coding_feedback = coding_feedback.strip()[-16_000:]
        coding_options = {"feedback": coding_feedback}
        if task_context:
            coding_options["task_context"] = task_context
        before_model = await worktree_fingerprint(workspace.path, self._git, is_generated)
        model_result = await self.coder.modify(requirement, plan, workspace.path, **coding_options)
        model_results.append(model_result)
        model_changed = (
            await worktree_fingerprint(workspace.path, self._git, is_generated)
            != before_model
        )
        changed_files = await self._changed_files(workspace.path)
        if not changed_files or (retest_feedback and not model_changed):
            if (
                revision
                and not retest_feedback
                and await self._has_implementation(workspace.path, workspace.base_commit)
            ):
                return await self._evidence_only_result(
                    run_id, revision, workspace, project, model_result
                )
            retry_feedback = no_change_feedback(model_result, feedback)
            if retest_feedback:
                retry_feedback = (
                    f"{retry_feedback}\n\nThe existing uncommitted implementation still "
                    "fails quality "
                    "checks. You must change the worktree to repair that failure before returning."
                )[-16_000:]
            retry_options = {"feedback": retry_feedback}
            if task_context:
                retry_options["task_context"] = task_context
            before_retry = await worktree_fingerprint(
                workspace.path, self._git, is_generated
            )
            model_result = await self.coder.modify(
                requirement, plan, workspace.path, **retry_options
            )
            model_results.append(model_result)
            retry_changed = (
                await worktree_fingerprint(workspace.path, self._git, is_generated)
                != before_retry
            )
            changed_files = await self._changed_files(workspace.path)
            if not changed_files or (retest_feedback and not retry_changed):
                attempts = "; ".join(
                    f"{item.provider}/{item.model}: {item.content.summary}"
                    for item in model_results
                )
                raise WorkerExecutionError(
                    "no_changes",
                    "Coding completed twice without changing tracked or untracked files. "
                    f"Model reports: {attempts}",
                    model_results=model_results,
                )
        forbidden = [name for name in changed_files if Path(name).name in FORBIDDEN_FILES]
        if forbidden:
            raise WorkerExecutionError("forbidden_files", ", ".join(forbidden))
        generated = [name for name in changed_files if is_generated(name)]
        if generated:
            raise WorkerExecutionError("generated_files", ", ".join(generated))

        scheduled = await self.test_scheduler.run(
            f"{run_id}-r{revision}",
            run_id,
            project.test_commands,
            project.test_timeout_seconds,
            workspace.path,
        )
        tests = scheduled.tests
        raise_for_failed_tests(tests, model_results=model_results)

        await self._cleanup_generated_untracked(workspace.path)
        await self._git(workspace.path, "add", "-A", "--", *changed_files)
        commit_metadata = f"TaskHub-Run: {run_id}\nTaskHub-Revision: {revision}"
        if task_context and task_context.get("task_id"):
            commit_metadata += f"\nTaskHub-Task: {task_context['task_id']}"
        if feedback:
            commit_metadata += f"\nTaskHub-Feedback: {feedback_key(feedback)}"
        await self._git(
            workspace.path,
            "-c",
            "user.name=TaskHub V2",
            "-c",
            "user.email=taskhub@local",
            "commit",
            "-m",
            f"taskhub: {model_result.content.summary[:120]}",
            "-m",
            commit_metadata,
        )
        commit = (await self._git(workspace.path, "rev-parse", "HEAD")).strip()
        diff = await self._git(
            workspace.path, "diff", "--binary", f"{workspace.base_commit}..{commit}"
        )
        aggregate_files = await self._git(
            workspace.path, "diff", "--name-only", f"{workspace.base_commit}..{commit}"
        )
        changed_files = [line for line in aggregate_files.splitlines() if line]
        artifact = self.artifacts.write_text(
            run_id, artifact_name(revision), "git_diff", diff
        )
        return ExecutionResult(
            summary=model_result.content.summary,
            evidence=diff[-50_000:],
            workspace=workspace,
            commit=commit,
            changed_files=changed_files,
            artifacts=[artifact],
            tests=tests,
            execution_node=scheduled.node_id,
            coding_node=str(model_result.usage.get("coding_node", "controller-31")),
            model_run=ModelRun(
                role="coder",
                provider=model_result.provider,
                model=model_result.model,
                duration_ms=model_result.duration_ms,
                failed_providers=model_result.failed_providers,
            ),
        )

    async def _has_implementation(self, workdir: str, base_commit: str) -> bool:
        head = (await self._git(workdir, "rev-parse", "HEAD")).strip()
        return head != base_commit

    async def _recover_uncommitted_if_valid(
        self, run_id, revision, workspace, project, feedback
    ) -> tuple[ExecutionResult | None, str]:
        changed_files = await self._changed_files(workspace.path)
        if not changed_files:
            return None, ""
        forbidden = [name for name in changed_files if Path(name).name in FORBIDDEN_FILES]
        generated = [name for name in changed_files if is_generated(name)]
        if forbidden or generated:
            return None, ""
        scheduled = await self.test_scheduler.run(
            # Stable identity lets node digests reuse an unchanged recovery attempt.
            f"{run_id}-r{revision}",
            run_id,
            project.test_commands,
            project.test_timeout_seconds,
            workspace.path,
        )
        if any(test.exit_code for test in scheduled.tests):
            detail, diagnostics = failed_test_diagnostics(scheduled.tests)
            if classify_test_failure(scheduled.tests) == "tests_timeout":
                raise WorkerExecutionError("tests_timeout", detail, diagnostics=diagnostics)
            return None, detail
        await self._cleanup_generated_untracked(workspace.path)
        changed_files = await self._changed_files(workspace.path)
        await self._git(workspace.path, "add", "-A", "--", *changed_files)
        commit_metadata = (
            f"TaskHub-Run: {run_id}\nTaskHub-Revision: {revision}\n"
            "TaskHub-Recovery: quality-retest"
        )
        if feedback:
            commit_metadata += f"\nTaskHub-Feedback: {feedback_key(feedback)}"
        await self._git(
            workspace.path,
            "-c",
            "user.name=TaskHub V2",
            "-c",
            "user.email=taskhub@local",
            "commit",
            "-m",
            "taskhub: recover implementation after successful quality retest",
            "-m",
            commit_metadata,
        )
        commit = (await self._git(workspace.path, "rev-parse", "HEAD")).strip()
        diff = await self._git(
            workspace.path, "diff", "--binary", f"{workspace.base_commit}..{commit}"
        )
        aggregate_files = await self._git(
            workspace.path, "diff", "--name-only", f"{workspace.base_commit}..{commit}"
        )
        artifact = self.artifacts.write_text(
            run_id, artifact_name(revision), "git_diff", diff
        )
        return ExecutionResult(
            summary="Recovered existing implementation after a successful quality retest",
            evidence=diff[-50_000:],
            workspace=workspace,
            commit=commit,
            changed_files=[line for line in aggregate_files.splitlines() if line],
            artifacts=[artifact],
            tests=scheduled.tests,
            execution_node=scheduled.node_id,
            coding_node="workspace-recovery",
        ), ""

    async def _evidence_only_result(
        self, run_id, revision, workspace, project, model_result
    ) -> ExecutionResult:
        scheduled = await self.test_scheduler.run(
            f"{run_id}-r{revision}",
            run_id,
            project.test_commands,
            project.test_timeout_seconds,
            workspace.path,
        )
        raise_for_failed_tests(scheduled.tests)
        commit = (await self._git(workspace.path, "rev-parse", "HEAD")).strip()
        diff = await self._git(
            workspace.path, "diff", "--binary", f"{workspace.base_commit}..{commit}"
        )
        changed = await self._git(
            workspace.path, "diff", "--name-only", f"{workspace.base_commit}..{commit}"
        )
        artifact = self.artifacts.write_text(
            run_id, artifact_name(revision), "git_diff", diff
        )
        return ExecutionResult(
            summary=model_result.content.summary,
            evidence=diff[-50_000:],
            workspace=workspace,
            commit=commit,
            changed_files=[line for line in changed.splitlines() if line],
            artifacts=[artifact],
            tests=scheduled.tests,
            execution_node=scheduled.node_id,
            coding_node=str(model_result.usage.get("coding_node", "controller-31")),
            model_run=ModelRun(
                role="coder",
                provider=model_result.provider,
                model=model_result.model,
                duration_ms=model_result.duration_ms,
                failed_providers=model_result.failed_providers,
            ),
        )

    async def _existing_result(
        self, workdir: str, base_commit: str, revision: int, feedback: str = ""
    ) -> ExecutionResult | None:
        status = (await self._git(workdir, "status", "--porcelain")).strip()
        head = (await self._git(workdir, "rev-parse", "HEAD")).strip()
        expected = ""
        if revision:
            expected = (
                await self._git(
                    workdir,
                    "log",
                    "-1",
                    "--format=%H",
                    "--fixed-strings",
                    f"--grep=TaskHub-Revision: {revision}",
                )
            ).strip()
        feedback_recorded = True
        if feedback and expected == head:
            message = await self._git(workdir, "log", "-1", "--format=%B")
            feedback_recorded = f"TaskHub-Feedback: {feedback_key(feedback)}" in message
        recoverable = (
            head != base_commit and (revision == 0 or expected == head) and feedback_recorded
        )
        if not status and recoverable:
            diff = await self._git(workdir, "diff", "--binary", f"{base_commit}..{head}")
            changed = await self._git(workdir, "diff", "--name-only", f"{base_commit}..{head}")
            return ExecutionResult(
                summary="Recovered previously committed execution",
                evidence=diff[-50_000:],
                commit=head,
                changed_files=[line for line in changed.splitlines() if line],
            )
        return None

    async def _changed_files(self, workdir: str) -> list[str]:
        output = await self._git(workdir, "status", "--porcelain")
        return [line[3:].strip() for line in output.splitlines() if len(line) > 3]

    async def _cleanup_generated_untracked(self, workdir: str) -> None:
        output = await self._git(workdir, "ls-files", "--others", "--exclude-standard", "-z")
        for name in output.split("\0"):
            if not name or not is_generated(name):
                continue
            target = Path(workdir, name)
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()

    @staticmethod
    async def _git(workdir: str, *arguments: str) -> str:
        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            workdir,
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode:
            raise WorkerExecutionError("git_failed", stderr.decode(errors="replace")[-1000:])
        return stdout.decode(errors="replace")
