import asyncio
import hashlib
import shutil
from pathlib import Path

from taskhub_v2.artifacts import ArtifactStore
from taskhub_v2.domain.models import ExecutionResult, ModelRun, Plan
from taskhub_v2.execution import LocalTestScheduler
from taskhub_v2.git import GitWorkspaceManager
from taskhub_v2.projects import ProjectRegistry
from taskhub_v2.workers.coding_router import CodexCodingRouter
from taskhub_v2.workers.test_diagnostics import failed_test_diagnostics

FORBIDDEN_FILES = {".env", ".env.local", "auth.json", "credentials.json"}
GENERATED_PARTS = {".venv", "__pycache__", ".pytest_cache", "node_modules", "dist", "build"}
GENERATED_SUFFIXES = {".pyc", ".pyo", ".coverage"}


class WorkerExecutionError(RuntimeError):
    def __init__(self, reason: str, detail: str, model_results=None, diagnostics=None):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail
        self.model_results = model_results or []
        self.diagnostics = diagnostics or []


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
            failed = [test for test in existing.tests if test.exit_code]
            if failed:
                detail, diagnostics = failed_test_diagnostics(existing.tests)
                raise WorkerExecutionError("tests_failed", detail, diagnostics=diagnostics)
            existing.artifacts = [
                self.artifacts.write_text(
                    run_id, self._artifact_name(revision), "git_diff", existing.evidence
                )
            ]
            return existing

        model_results = []
        coding_options = {"feedback": feedback}
        if task_context:
            coding_options["task_context"] = task_context
        model_result = await self.coder.modify(requirement, plan, workspace.path, **coding_options)
        model_results.append(model_result)
        changed_files = await self._changed_files(workspace.path)
        if not changed_files:
            if revision and await self._has_implementation(workspace.path, workspace.base_commit):
                return await self._evidence_only_result(
                    run_id, revision, workspace, project, model_result
                )
            retry_feedback = self._no_change_feedback(model_result, feedback)
            retry_options = {"feedback": retry_feedback}
            if task_context:
                retry_options["task_context"] = task_context
            model_result = await self.coder.modify(
                requirement, plan, workspace.path, **retry_options
            )
            model_results.append(model_result)
            changed_files = await self._changed_files(workspace.path)
            if not changed_files:
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
        generated = [name for name in changed_files if self._is_generated(name)]
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
        failed = [test for test in tests if test.exit_code]
        if failed:
            detail, diagnostics = failed_test_diagnostics(tests)
            raise WorkerExecutionError("tests_failed", detail, diagnostics=diagnostics)

        await self._cleanup_generated_untracked(workspace.path)
        await self._git(workspace.path, "add", "-A", "--", *changed_files)
        commit_metadata = f"TaskHub-Run: {run_id}\nTaskHub-Revision: {revision}"
        if task_context and task_context.get("task_id"):
            commit_metadata += f"\nTaskHub-Task: {task_context['task_id']}"
        if feedback:
            commit_metadata += f"\nTaskHub-Feedback: {self._feedback_key(feedback)}"
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
            run_id, self._artifact_name(revision), "git_diff", diff
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

    @staticmethod
    def _no_change_feedback(model_result, existing_feedback: str) -> str:
        prefix = f"{existing_feedback}\n" if existing_feedback else ""
        return (
            prefix + "The previous coding attempt returned successfully but produced no Git "
            "changes. Its summary was: "
            + model_result.content.summary
            + ". Re-inspect the repository, identify the concrete files required by the "
            "approved plan, implement the missing behavior now, and verify that `git status "
            "--short` lists the edits before returning. Do not merely describe or validate "
            "the existing implementation."
        )

    async def _has_implementation(self, workdir: str, base_commit: str) -> bool:
        head = (await self._git(workdir, "rev-parse", "HEAD")).strip()
        return head != base_commit

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
        failed = [test for test in scheduled.tests if test.exit_code]
        if failed:
            detail, diagnostics = failed_test_diagnostics(scheduled.tests)
            raise WorkerExecutionError("tests_failed", detail, diagnostics=diagnostics)
        commit = (await self._git(workspace.path, "rev-parse", "HEAD")).strip()
        diff = await self._git(
            workspace.path, "diff", "--binary", f"{workspace.base_commit}..{commit}"
        )
        changed = await self._git(
            workspace.path, "diff", "--name-only", f"{workspace.base_commit}..{commit}"
        )
        artifact = self.artifacts.write_text(
            run_id, self._artifact_name(revision), "git_diff", diff
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
            feedback_recorded = f"TaskHub-Feedback: {self._feedback_key(feedback)}" in message
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

    @staticmethod
    def _feedback_key(feedback: str) -> str:
        return hashlib.sha256(feedback.encode()).hexdigest()[:16]

    @staticmethod
    def _artifact_name(revision: int) -> str:
        return "change.patch" if revision == 0 else f"change-r{revision}.patch"

    async def _changed_files(self, workdir: str) -> list[str]:
        output = await self._git(workdir, "status", "--porcelain")
        return [line[3:].strip() for line in output.splitlines() if len(line) > 3]

    @staticmethod
    def _is_generated(name: str) -> bool:
        path = Path(name)
        return (
            bool(GENERATED_PARTS.intersection(path.parts))
            or path.suffix in GENERATED_SUFFIXES
            or path.name == ".coverage"
        )

    async def _cleanup_generated_untracked(self, workdir: str) -> None:
        output = await self._git(workdir, "ls-files", "--others", "--exclude-standard", "-z")
        for name in output.split("\0"):
            if not name or not self._is_generated(name):
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
