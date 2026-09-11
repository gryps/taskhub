from __future__ import annotations

import re
from collections import defaultdict, deque
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel, Field

from taskhub_v2.domain.models import Plan
from taskhub_v2.domain.production import (
    ExecutionPlan,
    ExecutionPlanStatus,
    ProductionTask,
    ProductSpec,
)
from taskhub_v2.domain.project_contract import ProjectContract
from taskhub_v2.persistence.production import ProductionStore


class DagValidationFinding(BaseModel):
    code: str
    task_id: str = ""
    detail: str


class DagPlanValidationError(ValueError):
    def __init__(self, findings: list[DagValidationFinding]):
        self.findings = findings
        super().__init__("; ".join(item.detail for item in findings[:10]))


class DagPlanBundle(BaseModel):
    plan: ExecutionPlan
    tasks: list[ProductionTask]
    topological_order: list[str]
    findings: list[DagValidationFinding] = Field(default_factory=list)


class DagPlanService:
    def __init__(self, store: ProductionStore, capability_inventory=None):
        self.store = store
        self.capability_inventory = capability_inventory

    async def compile_from_run(
        self,
        state: dict[str, Any],
        model_plan: Plan,
        *,
        actor: str = "planner",
    ) -> DagPlanBundle:
        existing = await self.for_run(state["project_id"], state["run_id"])
        if existing and existing.plan.status in {
            ExecutionPlanStatus.ACTIVE,
            ExecutionPlanStatus.COMPLETED,
        }:
            return existing
        spec = await self.store.get(
            "product_spec", state["product_spec_id"], str(state["product_spec_version"])
        )
        contract_context = (state.get("project_contract") or {}).get("contract")
        if not isinstance(spec, ProductSpec) or not contract_context:
            raise DagPlanValidationError(
                [DagValidationFinding(code="source_missing", detail="缺少已批准规格或项目契约")]
            )
        contract = ProjectContract.model_validate(contract_context)
        return await self.compile(
            state["project_id"], state["run_id"], spec, contract, model_plan, actor=actor
        )

    async def compile(
        self,
        project_id: str,
        run_id: str,
        spec: ProductSpec,
        contract: ProjectContract,
        model_plan: Plan,
        *,
        actor: str = "planner",
    ) -> DagPlanBundle:
        suffix = re.sub(r"[^A-Za-z0-9]", "", run_id)[-20:] or "run"
        plan_id = f"plan_{suffix}"
        tasks = self._tasks(project_id, plan_id, spec, contract, model_plan, actor)
        findings, order = await self.validate(tasks, contract)
        if findings:
            raise DagPlanValidationError(findings)
        plan = ExecutionPlan(
            project_id=project_id,
            plan_id=plan_id,
            version=1,
            status=ExecutionPlanStatus.DRAFT,
            product_spec_id=spec.spec_id,
            product_spec_version=spec.version,
            project_contract_id=contract.contract_id,
            project_contract_version=contract.version,
            run_id=run_id,
            task_ids=[item.task_id for item in tasks],
            milestones=[{"id": "delivery", "task_ids": order}],
            policy={"project_concurrency": 2, "priority": 100, "dynamic_batches": True},
            created_by=actor,
        )
        persisted = await self.store.get("execution_plan", plan.plan_id, str(plan.version))
        if isinstance(persisted, ExecutionPlan):
            plan = persisted
        else:
            plan = await self.store.save(plan)
        for task in tasks:
            if not await self.store.get("task", task.task_id, "1"):
                await self.store.save(task)
        if plan.status == ExecutionPlanStatus.DRAFT:
            plan = await self.store.save(
                plan.model_copy(update={"status": ExecutionPlanStatus.VALIDATING})
            )
        if plan.status == ExecutionPlanStatus.VALIDATING:
            plan = await self.store.save(
                plan.model_copy(update={"status": ExecutionPlanStatus.ACTIVE})
            )
        return DagPlanBundle(plan=plan, tasks=tasks, topological_order=order)

    async def validate(
        self, tasks: list[ProductionTask], contract: ProjectContract
    ) -> tuple[list[DagValidationFinding], list[str]]:
        findings: list[DagValidationFinding] = []
        by_id = {item.task_id: item for item in tasks}
        if len(by_id) != len(tasks):
            findings.append(DagValidationFinding(code="duplicate_task", detail="任务编号重复"))
        for task in tasks:
            for dependency in task.depends_on:
                if dependency not in by_id:
                    findings.append(
                        DagValidationFinding(
                            code="unknown_dependency",
                            task_id=task.task_id,
                            detail=f"{task.task_id} 引用了不存在的依赖 {dependency}",
                        )
                    )
            if task.task_type == "implementation" and not task.acceptance_commands:
                findings.append(
                    DagValidationFinding(
                        code="acceptance_missing",
                        task_id=task.task_id,
                        detail=f"{task.task_id} 没有可执行验收命令",
                    )
                )
            if task.estimated_size == "large" and not task.independently_verifiable:
                findings.append(
                    DagValidationFinding(
                        code="task_too_large",
                        task_id=task.task_id,
                        detail=f"{task.task_id} 过大且不能独立验证",
                    )
                )
            findings.extend(self._contract_findings(task, contract))
        order = self._topological_order(tasks)
        if len(order) != len(tasks):
            findings.append(DagValidationFinding(code="dependency_cycle", detail="任务依赖存在环"))
        findings.extend(self._interface_findings(tasks))
        findings.extend(self._dataflow_findings(tasks))
        findings.extend(
            await self._capability_findings(
                tasks, install_available=bool(contract.commands.install)
            )
        )
        return findings, order

    async def for_run(self, project_id: str, run_id: str) -> DagPlanBundle | None:
        records = await self.store.list(project_id=project_id)
        plans = [
            item for item in records if isinstance(item, ExecutionPlan) and item.run_id == run_id
        ]
        if not plans:
            return None
        plan = max(plans, key=lambda item: item.version)
        tasks = [
            item
            for item in records
            if isinstance(item, ProductionTask)
            and item.plan_id == plan.plan_id
            and item.plan_version == plan.version
        ]
        return DagPlanBundle(
            plan=plan,
            tasks=sorted(tasks, key=lambda item: plan.task_ids.index(item.task_id)),
            topological_order=self._topological_order(tasks),
        )

    def _tasks(
        self,
        project_id: str,
        plan_id: str,
        spec: ProductSpec,
        contract: ProjectContract,
        model_plan: Plan,
        actor: str,
    ) -> list[ProductionTask]:
        steps = model_plan.steps or [model_plan.summary]
        task_ids = [
            f"task_{plan_id.removeprefix('plan_')}_{index:03d}"
            for index in range(1, len(steps) + 1)
        ]
        verification_words = ("test", "verify", "check", "accept", "测试", "验证", "检查", "验收")
        implementation_ids = [
            task_ids[index]
            for index, step in enumerate(steps)
            if not any(word in step.lower() for word in verification_words)
        ]
        commands = [
            *contract.commands.test,
            *contract.commands.architecture,
            *contract.commands.integration,
        ]
        tasks = []
        for index, (task_id, step) in enumerate(zip(task_ids, steps, strict=True)):
            verification = any(word in step.lower() for word in verification_words)
            module = contract.modules[index % len(contract.modules)]
            paths = [] if verification else module.paths
            dependencies = implementation_ids if verification else []
            dependencies = [item for item in dependencies if item != task_id]
            locks = sorted(
                {f"path:{self._lock_path(path)}" for path in paths if self._lock_path(path)}
            )
            if any(word in step.lower() for word in ("migration", "schema", "迁移", "数据库")):
                locks.append("database:migrations")
            tasks.append(
                ProductionTask(
                    project_id=project_id,
                    task_id=task_id,
                    plan_id=plan_id,
                    plan_version=1,
                    title=step[:200],
                    objective=(
                        f"{step}\nProduct goal: "
                        f"{spec.goals[0] if spec.goals else model_plan.summary}"
                    )[:4_000],
                    task_type="verification" if verification else "implementation",
                    depends_on=dependencies,
                    inputs=[
                        {
                            "source_task_id": item,
                            "source_output": f"{item}:change_bundle",
                            "contract": "change_bundle",
                        }
                        for item in dependencies
                    ],
                    outputs=[{"id": f"{task_id}:change_bundle", "kind": "deliverable"}],
                    allowed_paths=paths,
                    forbidden_paths=contract.repository_policy.forbidden_globs,
                    required_capabilities=self._capabilities(contract, verification),
                    contracts=[f"{contract.contract_id}:v{contract.version}"],
                    acceptance_commands=commands,
                    expected_artifacts=contract.artifacts.required_artifacts,
                    resource_locks=locks,
                    priority=100 + index,
                    created_by=actor,
                )
            )
        return tasks

    @staticmethod
    def _topological_order(tasks: list[ProductionTask]) -> list[str]:
        by_id = {item.task_id: item for item in tasks}
        degree = {
            item.task_id: len([dep for dep in item.depends_on if dep in by_id]) for item in tasks
        }
        children: dict[str, list[str]] = defaultdict(list)
        for item in tasks:
            for dependency in item.depends_on:
                children[dependency].append(item.task_id)
        queue = deque(sorted(task_id for task_id, count in degree.items() if count == 0))
        order = []
        while queue:
            current = queue.popleft()
            order.append(current)
            for child in children[current]:
                degree[child] -= 1
                if degree[child] == 0:
                    queue.append(child)
        return order

    @staticmethod
    def _contract_findings(
        task: ProductionTask, contract: ProjectContract
    ) -> list[DagValidationFinding]:
        allowed_roots = [
            DagPlanService._lock_path(path) for module in contract.modules for path in module.paths
        ]
        return [
            DagValidationFinding(
                code="contract_path_violation",
                task_id=task.task_id,
                detail=f"{task.task_id} 的修改范围 {path} 不属于已批准项目契约",
            )
            for path in task.allowed_paths
            if not any(DagPlanService._paths_overlap(path, root) for root in allowed_roots)
        ]

    @staticmethod
    def _interface_findings(tasks: list[ProductionTask]) -> list[DagValidationFinding]:
        findings = []
        for index, left in enumerate(tasks):
            for right in tasks[index + 1 :]:
                if left.task_id in right.depends_on or right.task_id in left.depends_on:
                    continue
                overlaps = [
                    path
                    for path in left.allowed_paths
                    if any(
                        DagPlanService._paths_overlap(path, other) for other in right.allowed_paths
                    )
                ]
                if overlaps and not set(left.resource_locks) & set(right.resource_locks):
                    findings.append(
                        DagValidationFinding(
                            code="unsafe_parallel_overlap",
                            task_id=right.task_id,
                            detail=f"{left.task_id} 与 {right.task_id} 修改范围重叠却没有共享锁",
                        )
                    )
        return findings

    @staticmethod
    def _dataflow_findings(tasks: list[ProductionTask]) -> list[DagValidationFinding]:
        by_id = {item.task_id: item for item in tasks}
        consumed = {
            item.get("source_output", "")
            for task in tasks
            for item in task.inputs
            if item.get("source_output")
        }
        findings = []
        for task in tasks:
            for item in task.inputs:
                source_id = item.get("source_task_id", "")
                source_output = item.get("source_output", "")
                source = by_id.get(source_id)
                available = {output.get("id", "") for output in source.outputs} if source else set()
                if source_id not in task.depends_on or source_output not in available:
                    findings.append(
                        DagValidationFinding(
                            code="input_source_missing",
                            task_id=task.task_id,
                            detail=(
                                f"{task.task_id} 的输入 {source_output or source_id} 没有有效来源"
                            ),
                        )
                    )
            for output in task.outputs:
                output_id = output.get("id", "")
                if output.get("kind") != "deliverable" and output_id not in consumed:
                    findings.append(
                        DagValidationFinding(
                            code="output_unconsumed",
                            task_id=task.task_id,
                            detail=f"{task.task_id} 的输出 {output_id} 没有消费者",
                        )
                    )
        return findings

    async def _capability_findings(
        self, tasks: list[ProductionTask], *, install_available: bool
    ) -> list[DagValidationFinding]:
        if self.capability_inventory is None or install_available:
            return []
        available = set(await self.capability_inventory())
        return [
            DagValidationFinding(
                code="capability_unavailable",
                task_id=task.task_id,
                detail=f"{task.task_id} 缺少可用节点能力：{', '.join(missing)}",
            )
            for task in tasks
            if (missing := sorted(set(task.required_capabilities) - available))
        ]

    @staticmethod
    def _capabilities(contract: ProjectContract, verification: bool) -> list[str]:
        capabilities = [] if verification else ["coding"]
        if "python" in contract.languages:
            capabilities.append("python3")
        if any(language in {"javascript", "typescript"} for language in contract.languages):
            capabilities.append("node")
        if verification and contract.commands.test:
            capabilities.append("pytest" if "python" in contract.languages else "npm")
        return sorted(set(capabilities))

    @staticmethod
    def _lock_path(path: str) -> str:
        return path.split("*")[0].rstrip("/")

    @staticmethod
    def _paths_overlap(left: str, right: str) -> bool:
        left_path = PurePosixPath(DagPlanService._lock_path(left))
        right_path = PurePosixPath(DagPlanService._lock_path(right))
        return (
            left_path == right_path
            or left_path in right_path.parents
            or right_path in left_path.parents
        )
