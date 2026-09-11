from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from taskhub_v2.domain.topology import (
    ProductionTopology,
    TopologyEdgeType,
    TopologyNode,
    TopologyNodeType,
    TopologyStatus,
    ValidationFinding,
)


class TopologyNotFoundError(LookupError):
    pass


class TopologyConflictError(RuntimeError):
    pass


LEGAL_EDGES = {
    TopologyEdgeType.GOVERNED_BY: {(TopologyNodeType.PROJECT, TopologyNodeType.CONTROLLER)},
    TopologyEdgeType.EXECUTES_ON: {
        (TopologyNodeType.PROJECT, TopologyNodeType.EXECUTION),
        (TopologyNodeType.PROJECT, TopologyNodeType.RESOURCE_POOL),
        (TopologyNodeType.CONTROLLER, TopologyNodeType.EXECUTION),
        (TopologyNodeType.CONTROLLER, TopologyNodeType.RESOURCE_POOL),
    },
    TopologyEdgeType.VERIFIED_BY: {
        (TopologyNodeType.PROJECT, TopologyNodeType.TEST),
        (TopologyNodeType.PROJECT, TopologyNodeType.RESOURCE_POOL),
        (TopologyNodeType.EXECUTION, TopologyNodeType.TEST),
        (TopologyNodeType.EXECUTION, TopologyNodeType.RESOURCE_POOL),
        (TopologyNodeType.RESOURCE_POOL, TopologyNodeType.TEST),
        (TopologyNodeType.RESOURCE_POOL, TopologyNodeType.RESOURCE_POOL),
    },
    TopologyEdgeType.ACCEPTED_BY: {
        (TopologyNodeType.PROJECT, TopologyNodeType.PREPRODUCTION)
    },
    TopologyEdgeType.FALLBACK_TO: {
        (kind, target)
        for kind in {
            TopologyNodeType.EXECUTION,
            TopologyNodeType.TEST,
            TopologyNodeType.PREPRODUCTION,
            TopologyNodeType.RESOURCE_POOL,
        }
        for target in {
            TopologyNodeType.EXECUTION,
            TopologyNodeType.TEST,
            TopologyNodeType.PREPRODUCTION,
            TopologyNodeType.RESOURCE_POOL,
        }
    },
}


class TopologyService:
    def __init__(self, store, projects, scheduler):
        self.store = store
        self.projects = projects
        self.scheduler = scheduler

    async def list(self, project_id: str) -> list[ProductionTopology]:
        self.projects.get(project_id)
        return await self.store.list(project_id)

    async def current(self, project_id: str) -> ProductionTopology | None:
        records = await self.list(project_id)
        return next((item for item in records if item.status == TopologyStatus.ACTIVE), None)

    async def create_draft(self, project_id: str, actor: str) -> ProductionTopology:
        project = self.projects.get(project_id)
        records = await self.store.list(project_id)
        existing = next((item for item in records if item.status == TopologyStatus.DRAFT), None)
        if existing:
            return existing
        version = max((item.version for item in records), default=0) + 1
        source = next((item for item in records if item.status == TopologyStatus.ACTIVE), None)
        values = dict(
            topology_id=f"topology_{project_id}",
            project_id=project_id,
            version=version,
            nodes=(
                [item.model_copy(deep=True) for item in source.nodes]
                if source
                else [
                    TopologyNode(
                        node_id=f"project:{project_id}",
                        node_type=TopologyNodeType.PROJECT,
                        label=project.name,
                        resource_id=project_id,
                    ),
                    TopologyNode(
                        node_id="controller:seed",
                        node_type=TopologyNodeType.CONTROLLER,
                        label="Seed 控制节点",
                        resource_id="seed-controller",
                    ),
                ]
            ),
            edges=[item.model_copy(deep=True) for item in source.edges] if source else [],
            created_by=actor,
        )
        if source:
            values["viewport"] = source.viewport.model_copy()
        topology = ProductionTopology(**values)
        return await self.store.save(topology)

    async def update(
        self,
        project_id: str,
        topology_id: str,
        version: int,
        *,
        nodes,
        edges,
        viewport,
        expected_digest: str,
    ) -> ProductionTopology:
        topology = await self._get(project_id, topology_id, version)
        if topology.status != TopologyStatus.DRAFT:
            raise TopologyConflictError("only draft topology versions can be edited")
        if expected_digest and topology.content_digest != expected_digest:
            raise TopologyConflictError("topology changed; reload before saving")
        return await self.store.save(
            topology.model_copy(
                update={"nodes": nodes, "edges": edges, "viewport": viewport, "findings": []}
            )
        )

    async def validate(
        self, project_id: str, topology_id: str, version: int
    ) -> ProductionTopology:
        topology = await self._get(project_id, topology_id, version)
        if topology.status != TopologyStatus.DRAFT:
            raise TopologyConflictError("only a draft topology can enter validation")
        findings = await self._findings(topology)
        status = (
            TopologyStatus.INVALID
            if any(item.level == "error" for item in findings)
            else TopologyStatus.VALIDATING
        )
        return await self.store.save(
            topology.model_copy(update={"status": status, "findings": findings})
        )

    async def activate(
        self, project_id: str, topology_id: str, version: int, actor: str
    ) -> ProductionTopology:
        topology = await self._get(project_id, topology_id, version)
        if topology.status != TopologyStatus.VALIDATING:
            raise TopologyConflictError("topology must pass validation before activation")
        findings = await self._findings(topology)
        if any(item.level == "error" for item in findings):
            raise TopologyConflictError("topology resources changed; validate a new version")
        return await self.store.activate(
            topology.model_copy(
                update={
                    "status": TopologyStatus.ACTIVE,
                    "findings": findings,
                    "activated_by": actor,
                    "activated_at": datetime.now(UTC),
                }
            )
        )

    async def eligible_node_ids(self, project_id: str, workload: str) -> set[str] | None:
        topology = await self.current(project_id)
        if topology is None:
            return None
        edge_types = (
            {TopologyEdgeType.VERIFIED_BY}
            if workload in {"test", "acceptance", "browser_acceptance"}
            else {TopologyEdgeType.EXECUTES_ON}
        )
        nodes = {item.node_id: item for item in topology.nodes}
        selected: set[str] = set()
        for edge in sorted(topology.edges, key=lambda item: item.priority):
            if edge.edge_type not in edge_types:
                continue
            target = nodes.get(edge.target)
            if not target:
                continue
            if target.node_type == TopologyNodeType.RESOURCE_POOL:
                selected.update(target.member_ids)
            elif target.resource_id:
                selected.add(target.resource_id)
        return selected

    async def runtime(self, project_id: str, run_service, dag_runtime) -> dict:
        topology = await self.current(project_id)
        page = await run_service.list(project_id=project_id, page=1, page_size=1)
        if not page.items:
            return {"topology": topology, "run": None, "execution": None, "nodes": []}
        run = await run_service.get(page.items[0].run_id)
        execution = await dag_runtime.view(project_id, run.run_id) if dag_runtime else None
        statuses = await self.scheduler.status()
        return {"topology": topology, "run": run, "execution": execution, "nodes": statuses}

    async def _get(self, project_id, topology_id, version) -> ProductionTopology:
        topology = await self.store.get(topology_id, version)
        if not topology or topology.project_id != project_id:
            raise TopologyNotFoundError(topology_id)
        return topology

    async def _findings(self, topology: ProductionTopology) -> list[ValidationFinding]:
        findings: list[ValidationFinding] = []
        nodes = {item.node_id: item for item in topology.nodes}
        if len(nodes) != len(topology.nodes):
            findings.append(self._error("duplicate_node", "节点 ID 必须唯一"))
        edge_ids = {item.edge_id for item in topology.edges}
        if len(edge_ids) != len(topology.edges):
            findings.append(self._error("duplicate_edge", "连线 ID 必须唯一"))
        for edge in topology.edges:
            source, target = nodes.get(edge.source), nodes.get(edge.target)
            if not source or not target:
                findings.append(self._error("missing_endpoint", "连线端点不存在", edge.edge_id))
            elif (source.node_type, target.node_type) not in LEGAL_EDGES[edge.edge_type]:
                findings.append(
                    self._error("illegal_edge", "该节点类型不允许使用此连线", edge.edge_id)
                )
            elif edge.edge_type == TopologyEdgeType.FALLBACK_TO:
                source_role = self._resource_role(source, nodes)
                target_role = self._resource_role(target, nodes)
                if source_role and target_role and source_role != target_role:
                    findings.append(
                        self._error(
                            "fallback_role", "故障转移只能连接相同角色资源", edge.edge_id
                        )
                    )
        project_nodes = [item for item in topology.nodes if item.node_type == "project"]
        if len(project_nodes) != 1 or project_nodes[0].resource_id != topology.project_id:
            findings.append(self._error("project_binding", "拓扑必须唯一绑定当前项目"))
        governed = [item for item in topology.edges if item.edge_type == "governed_by"]
        if len(governed) != 1:
            findings.append(self._error("controller_path", "项目必须有且只有一条控制路径"))
        for edge_type, code, message in (
            ("executes_on", "execution_route", "至少配置一个执行节点或资源池"),
            ("verified_by", "test_route", "至少配置一个测试节点或资源池"),
        ):
            if not any(item.edge_type == edge_type for item in topology.edges):
                findings.append(self._error(code, message))
        findings.extend(self._cycle_findings(topology))
        statuses = {item.get("node_id"): item for item in await self.scheduler.status()}
        resource_bindings = [
            resource_id
            for node in topology.nodes
            for resource_id in (
                node.member_ids if node.node_type == "resource_pool" else [node.resource_id]
            )
            if resource_id and node.node_type not in {"project", "controller"}
        ]
        if len(resource_bindings) != len(set(resource_bindings)):
            findings.append(self._error("duplicate_binding", "同一资源不能重复绑定到多个节点"))
        for node in topology.nodes:
            if node.node_type == TopologyNodeType.RESOURCE_POOL and not node.member_ids:
                findings.append(self._error("empty_pool", "资源池至少需要一个成员", node.node_id))
        expected_route_workload = {
            TopologyEdgeType.EXECUTES_ON: "coding",
            TopologyEdgeType.VERIFIED_BY: "test",
            TopologyEdgeType.ACCEPTED_BY: "acceptance",
        }
        for edge in topology.edges:
            target = nodes.get(edge.target)
            workload = expected_route_workload.get(edge.edge_type)
            if not target or target.node_type != TopologyNodeType.RESOURCE_POOL or not workload:
                continue
            mismatched = [
                member
                for member in target.member_ids
                if workload not in set(statuses.get(member, {}).get("workloads", []))
            ]
            if mismatched:
                findings.append(
                    self._error(
                        "pool_role",
                        f"资源池成员不支持 {workload}：{', '.join(mismatched)}",
                        target.node_id,
                    )
                )
        expected = {
            TopologyNodeType.EXECUTION: "coding",
            TopologyNodeType.TEST: "test",
            TopologyNodeType.PREPRODUCTION: "acceptance",
        }
        for node in topology.nodes:
            bindings = node.member_ids if node.node_type == "resource_pool" else [node.resource_id]
            for resource_id in filter(None, bindings):
                if node.node_type in {TopologyNodeType.PROJECT, TopologyNodeType.CONTROLLER}:
                    continue
                status = statuses.get(resource_id)
                if not status:
                    findings.append(
                        self._error("resource_missing", "绑定资源不存在", node.node_id)
                    )
                elif status.get("status") != "ok":
                    findings.append(
                        self._error("resource_unhealthy", "绑定资源当前不健康", node.node_id)
                    )
                elif expected.get(node.node_type) and expected[node.node_type] not in set(
                    status.get("workloads", [])
                ):
                    findings.append(
                        self._error("capability_mismatch", "资源角色能力不匹配", node.node_id)
                    )
                elif not set(node.required_capabilities).issubset(
                    {
                        name
                        for name, available in status.get("capabilities", {}).items()
                        if available
                    }
                ):
                    findings.append(
                        self._error("capability_missing", "资源缺少节点要求的能力", node.node_id)
                    )
        active = await self.current(topology.project_id)
        if active and {item.resource_id for item in active.nodes} != {
            item.resource_id for item in topology.nodes
        }:
            findings.append(
                ValidationFinding(
                    code="inflight_unchanged",
                    message="新版本只影响后续调度，运行中任务保持原节点",
                    level="warning",
                )
            )
        return findings

    @staticmethod
    def _resource_role(node, nodes):
        if node.node_type != TopologyNodeType.RESOURCE_POOL:
            return str(node.node_type)
        roles = {
            str(item.node_type)
            for item in nodes.values()
            if item.resource_id in set(node.member_ids)
        }
        return next(iter(roles)) if len(roles) == 1 else ""

    @staticmethod
    def _cycle_findings(topology):
        graph = defaultdict(list)
        for edge in topology.edges:
            graph[edge.source].append(edge.target)
        visiting, visited = set(), set()

        def visit(node):
            if node in visiting:
                return True
            if node in visited:
                return False
            visiting.add(node)
            cycle = any(visit(target) for target in graph[node])
            visiting.remove(node)
            visited.add(node)
            return cycle

        if any(visit(node.node_id) for node in topology.nodes):
            return [TopologyService._error("cycle", "生产拓扑不能包含有向环路")]
        return []

    @staticmethod
    def _error(code, message, subject_id=""):
        return ValidationFinding(code=code, message=message, level="error", subject_id=subject_id)
