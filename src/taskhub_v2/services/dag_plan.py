from __future__ import annotations

import re
from collections import defaultdict, deque
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
from taskhub_v2.services.dag_validation import (
    DagValidationFinding,
    contract_findings,
    dataflow_findings,
    interface_findings,
    lock_path,
)


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
    def __init__(
        self,
        store: ProductionStore,
        capability_inventory=None,
        design_contract_resolver=None,
        project_policy_resolver=None,
    ):
        self.store = store
        self.capability_inventory = capability_inventory
        self.design_contract_resolver = design_contract_resolver
        self.project_policy_resolver = project_policy_resolver

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
        if contract.profile_id in {"fullstack-web", "frontend-spa"} and not (
            spec.capability_pack_lock
        ):
            raise DagPlanValidationError(
                [
                    DagValidationFinding(
                        code="capability_lock_missing",
                        detail="前端项目必须先锁定兼容的设计能力包版本",
                    )
                ]
            )
        design_source = (
            await self.design_contract_resolver(
                project_id, spec.capability_pack_lock, spec.spec_id, spec.version
            )
            if self.design_contract_resolver and spec.capability_pack_lock
            else None
        )
        if spec.capability_pack_lock and not design_source:
            raise DagPlanValidationError(
                [
                    DagValidationFinding(
                        code="design_contract_missing",
                        detail="规格锁定的能力包没有对应项目设计合同",
                    )
                ]
            )
        tasks = self._tasks(project_id, plan_id, spec, contract, model_plan, actor, design_source)
        findings, order = await self.validate(tasks, contract)
        if findings:
            raise DagPlanValidationError(findings)
        project_policy = (
            self.project_policy_resolver(project_id).scheduling_policy
            if self.project_policy_resolver
            else None
        )
        plan = ExecutionPlan(
            project_id=project_id,
            plan_id=plan_id,
            version=1,
            status=ExecutionPlanStatus.DRAFT,
            product_spec_id=spec.spec_id,
            product_spec_version=spec.version,
            project_contract_id=contract.contract_id,
            project_contract_version=contract.version,
            capability_lock_id=design_source[0].lock_id if design_source else "",
            capability_lock_version=design_source[0].version if design_source else None,
            design_contract_id=design_source[1].contract_id if design_source else "",
            design_contract_version=design_source[1].version if design_source else None,
            run_id=run_id,
            task_ids=[item.task_id for item in tasks],
            milestones=[{"id": "delivery", "task_ids": order}],
            policy={
                "project_concurrency": project_policy.concurrency_limit if project_policy else 2,
                "priority_weight": project_policy.priority_weight if project_policy else 1,
                "run_cost_budget_units": (
                    project_policy.run_cost_budget_units if project_policy else 100
                ),
                "dynamic_batches": True,
            },
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
            findings.extend(contract_findings(task, contract))
        order = self._topological_order(tasks)
        if len(order) != len(tasks):
            findings.append(DagValidationFinding(code="dependency_cycle", detail="任务依赖存在环"))
        findings.extend(interface_findings(tasks))
        findings.extend(dataflow_findings(tasks))
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
        design_source=None,
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
            locks = sorted({f"path:{lock_path(path)}" for path in paths if lock_path(path)})
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
                    contracts=[
                        f"{contract.contract_id}:v{contract.version}",
                        *(
                            [f"{design_source[1].contract_id}:v{design_source[1].version}"]
                            if design_source
                            else []
                        ),
                    ],
                    acceptance_commands=commands,
                    required_evidence=(
                        design_source[1].validation_evidence if design_source else []
                    ),
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
