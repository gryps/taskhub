from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from taskhub_v2.domain.capability import ProjectDesignContract
from taskhub_v2.domain.models import ExecutionResult, Plan
from taskhub_v2.domain.production import ExecutionPlan, ProductSpec, TaskAttempt
from taskhub_v2.domain.project_contract import ProjectContract
from taskhub_v2.git import GitWorkspaceManager
from taskhub_v2.persistence.production import ProductionStore
from taskhub_v2.projects import ProjectRegistry


class DagIntegrationError(RuntimeError):
    pass


class WorkerDagExecutor:
    def __init__(
        self,
        worker,
        store: ProductionStore,
        projects: ProjectRegistry,
        workspaces: GitWorkspaceManager | None = None,
        capability_resolver=None,
        test_scheduler=None,
        topology_resolver=None,
    ):
        self.worker = worker
        self.store = store
        self.projects = projects
        self.workspaces = workspaces or getattr(worker, "workspaces", None)
        self.capability_resolver = capability_resolver
        self.test_scheduler = test_scheduler
        self.topology_resolver = topology_resolver

    async def capability_reason(self, task) -> str:
        if self.workspaces is None or not self.capability_resolver:
            return ""
        workload = "test" if task.task_type == "verification" else "coding"
        available = await self.capability_resolver(
            task.project_id, task.required_capabilities, workload
        )
        return "" if available else "没有满足能力要求的健康节点"

    async def current_base(self, plan) -> str:
        if self.workspaces is None:
            return ""
        project = self.projects.get(plan.project_id)
        workspace = await self.workspaces.prepare(project, plan.run_id)
        return await asyncio.to_thread(self._git, Path(workspace.path), "rev-parse", "HEAD")

    async def execute(self, task, attempt: TaskAttempt, *, base_commit: str) -> ExecutionResult:
        if task.task_type == "verification" and self.workspaces and self.test_scheduler:
            return await self._verify(task, attempt, base_commit)
        plan = await self.store.get("execution_plan", task.plan_id, str(task.plan_version))
        if not isinstance(plan, ExecutionPlan):
            raise DagIntegrationError("任务绑定的执行计划不存在")
        spec = await self.store.get(
            "product_spec", plan.product_spec_id, str(plan.product_spec_version)
        )
        contract = await self.store.get(
            "project_contract", plan.project_contract_id, str(plan.project_contract_version)
        )
        if not isinstance(spec, ProductSpec) or not isinstance(contract, ProjectContract):
            raise DagIntegrationError("任务的规格或项目契约快照不存在")
        design = None
        if plan.design_contract_id:
            design = await self.store.get(
                "project_design_contract",
                plan.design_contract_id,
                str(plan.design_contract_version),
            )
            if not isinstance(design, ProjectDesignContract):
                raise DagIntegrationError("任务绑定的项目设计合同不存在")
        context = {
            "project_id": task.project_id,
            "task_id": task.task_id,
            "attempt_id": attempt.attempt_id,
            "objective": task.objective,
            "depends_on": task.depends_on,
            "inputs": task.inputs,
            "outputs": task.outputs,
            "allowed_paths": task.allowed_paths,
            "forbidden_paths": task.forbidden_paths,
            "required_capabilities": task.required_capabilities,
            "contracts": task.contracts,
            "acceptance_commands": task.acceptance_commands,
            "required_evidence": task.required_evidence,
            "expected_artifacts": task.expected_artifacts,
            "product_spec": {
                "id": spec.spec_id,
                "version": spec.version,
                "goals": spec.goals,
                "functional_requirements": spec.functional_requirements,
                "acceptance_criteria": spec.acceptance_criteria,
            },
            "project_contract": {
                "id": contract.contract_id,
                "version": contract.version,
                "profile": contract.profile_id,
            },
            "project_design_contract": (
                {
                    "id": design.contract_id,
                    "version": design.version,
                    "pack_refs": design.pack_refs,
                    "design_tokens": design.design_tokens,
                    "component_rules": design.component_rules,
                    "layout_rules": design.layout_rules,
                    "responsive_rules": design.responsive_rules,
                    "accessibility_rules": design.accessibility_rules,
                    "brand_rules": design.brand_rules,
                    "viewports": design.viewports,
                    "validation_evidence": design.validation_evidence,
                }
                if design
                else None
            ),
        }
        requirement = (
            "Complete only this DAG task. Respect all path and contract boundaries. "
            "Return verifiable changes and do not perform unrelated work.\n"
            + json.dumps(context, ensure_ascii=False, sort_keys=True)
        )
        task_plan = Plan(
            summary=task.title,
            steps=[task.objective],
            acceptance=[" ".join(command) for command in task.acceptance_commands],
        )
        return await self.worker.execute(
            attempt.attempt_id,
            task.project_id,
            requirement,
            task_plan,
            base_commit=base_commit,
            task_context=context,
        )

    async def _verify(self, task, attempt, base_commit: str) -> ExecutionResult:
        if not task.acceptance_commands:
            raise DagIntegrationError("验证任务没有可执行验收命令")
        project = self.projects.get(task.project_id)
        workspace = await self.workspaces.prepare(project, attempt.attempt_id, base_commit)
        scheduled = await self.test_scheduler.run(
            f"{attempt.attempt_id}-verify",
            task.task_id,
            task.acceptance_commands,
            project.test_timeout_seconds,
            workspace.path,
            workload="test",
            required_capabilities_override=set(task.required_capabilities),
            git_commit=base_commit,
            artifact_paths=task.expected_artifacts,
            eligible_node_ids=(
                await self.topology_resolver(task.project_id, "test")
                if self.topology_resolver
                else None
            ),
        )
        failed = [item for item in scheduled.tests if item.exit_code]
        if failed:
            detail = "; ".join(item.output_tail[-500:] for item in failed)
            raise DagIntegrationError(f"验证任务失败：{detail}")
        return ExecutionResult(
            summary=f"验证任务 {task.task_id} 已通过",
            evidence="\n".join(item.output_tail for item in scheduled.tests)[-50_000:],
            tests=scheduled.tests,
            artifacts=scheduled.artifacts,
            execution_node=scheduled.node_id,
        )

    async def integrate_batch(self, plan, results, *, base_commit: str) -> str:
        git_results = [item for item in results if item[2].workspace and item[2].commit]
        if not git_results:
            return base_commit
        if self.workspaces is None:
            raise DagIntegrationError("Git DAG 集成工作区未配置")
        project = self.projects.get(plan.project_id)
        workspace = await self.workspaces.prepare(project, plan.run_id)
        current = await asyncio.to_thread(self._git, Path(workspace.path), "rev-parse", "HEAD")
        if base_commit and current != base_commit:
            raise DagIntegrationError("DAG 集成基线已变化")
        for task, _attempt, result in git_results:
            contains = (
                subprocess.run(
                    [
                        "git",
                        "-C",
                        workspace.path,
                        "merge-base",
                        "--is-ancestor",
                        result.commit,
                        "HEAD",
                    ],
                    check=False,
                    capture_output=True,
                ).returncode
                == 0
            )
            if contains:
                continue
            try:
                await asyncio.to_thread(
                    self._git, Path(workspace.path), "cherry-pick", result.commit
                )
            except Exception as error:
                subprocess.run(
                    ["git", "-C", workspace.path, "cherry-pick", "--abort"],
                    check=False,
                    capture_output=True,
                )
                raise DagIntegrationError(
                    f"任务 {task.task_id} 集成冲突：{str(error)[:500]}"
                ) from error
        return await asyncio.to_thread(self._git, Path(workspace.path), "rev-parse", "HEAD")

    async def finalize(self, plan, tasks, attempts, *, base_commit: str) -> ExecutionResult:
        results = [
            ExecutionResult.model_validate(item.result)
            for item in attempts
            if item.result and str(item.status) == "validated"
        ]
        git_results = [item for item in results if item.workspace and item.commit]
        if not git_results or self.workspaces is None:
            return ExecutionResult(
                summary=f"{len(tasks)} 个 DAG 任务已完成",
                evidence="\n".join(item.evidence for item in results)[-50_000:],
                tests=[test for item in results for test in item.tests],
                artifacts=[artifact for item in results for artifact in item.artifacts],
                coding_node=",".join(
                    sorted({item.coding_node for item in results if item.coding_node})
                ),
            )
        project = self.projects.get(plan.project_id)
        workspace = await self.workspaces.prepare(project, plan.run_id)
        commit = await asyncio.to_thread(self._git, Path(workspace.path), "rev-parse", "HEAD")
        changed = await asyncio.to_thread(
            self._git,
            Path(workspace.path),
            "diff",
            "--name-only",
            f"{workspace.base_commit}..{commit}",
        )
        evidence = await asyncio.to_thread(
            self._git,
            Path(workspace.path),
            "diff",
            "--binary",
            f"{workspace.base_commit}..{commit}",
        )
        return ExecutionResult(
            summary=f"{len(tasks)} 个 DAG 任务已完成并集成",
            evidence=evidence[-50_000:],
            workspace=workspace,
            commit=commit,
            changed_files=[line for line in changed.splitlines() if line],
            tests=[test for item in results for test in item.tests],
            artifacts=[artifact for item in results for artifact in item.artifacts],
            coding_node=",".join(
                sorted({item.coding_node for item in results if item.coding_node})
            ),
        )

    @staticmethod
    def _git(repository: Path, *arguments: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode:
            raise DagIntegrationError(completed.stderr.strip()[:500] or "Git command failed")
        return completed.stdout.strip()
