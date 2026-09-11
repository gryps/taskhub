import asyncio
import json
from types import SimpleNamespace

import pytest

from taskhub_v2.domain.topology import (
    CanvasPosition,
    TopologyEdge,
    TopologyNode,
    TopologyNodeType,
    TopologyStatus,
)
from taskhub_v2.persistence.topologies import MemoryTopologyStore
from taskhub_v2.projects import ProjectRegistry
from taskhub_v2.services.topologies import TopologyConflictError, TopologyService
from taskhub_v2.workers.coding_router import ScheduledCodingRouter


class HealthyScheduler:
    async def status(self):
        return [
            {
                "node_id": "exec-a",
                "status": "ok",
                "workloads": ["coding", "build"],
                "capabilities": {"coding": True, "git": True},
                "slots": 2,
                "active": 0,
            },
            {
                "node_id": "exec-b",
                "status": "ok",
                "workloads": ["coding", "build"],
                "capabilities": {"coding": True, "git": True},
                "slots": 2,
                "active": 0,
            },
            {
                "node_id": "test-a",
                "status": "ok",
                "workloads": ["test", "acceptance"],
                "capabilities": {"pytest": True},
                "slots": 1,
                "active": 0,
            },
        ]


def project_registry(tmp_path):
    path = tmp_path / "projects.json"
    path.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "id": "shop",
                        "name": "Shop",
                        "repository": str(tmp_path),
                        "base_ref": "main",
                        "test_commands": [["pytest"]],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return ProjectRegistry(str(path))


def valid_nodes():
    return [
        TopologyNode(
            node_id="project:shop",
            node_type="project",
            label="Shop",
            resource_id="shop",
            position=CanvasPosition(x=0, y=0),
        ),
        TopologyNode(
            node_id="controller:seed",
            node_type="controller",
            label="Seed",
            resource_id="seed-controller",
            position=CanvasPosition(x=200, y=0),
        ),
        *[
            TopologyNode(
                node_id=f"execution:{name}",
                node_type="execution",
                label=name,
                resource_id=name,
                required_capabilities=["coding"],
                position=CanvasPosition(x=400, y=index * 160),
            )
            for index, name in enumerate(("exec-a", "exec-b"))
        ],
        TopologyNode(
            node_id="test:test-a",
            node_type="test",
            label="test-a",
            resource_id="test-a",
            required_capabilities=["pytest"],
            position=CanvasPosition(x=650, y=80),
        ),
    ]


def valid_edges():
    return [
        TopologyEdge(
            edge_id="governed", source="project:shop", target="controller:seed",
            edge_type="governed_by",
        ),
        TopologyEdge(
            edge_id="execute-a", source="controller:seed", target="execution:exec-a",
            edge_type="executes_on",
        ),
        TopologyEdge(
            edge_id="execute-b", source="controller:seed", target="execution:exec-b",
            edge_type="executes_on",
        ),
        TopologyEdge(
            edge_id="verify", source="execution:exec-a", target="test:test-a",
            edge_type="verified_by",
        ),
    ]


def test_topology_lifecycle_and_routing_survive_service_restart(tmp_path):
    async def scenario():
        store = MemoryTopologyStore()
        service = TopologyService(store, project_registry(tmp_path), HealthyScheduler())
        draft = await service.create_draft("shop", "owner")
        assert await service.create_draft("shop", "owner") == draft
        saved = await service.update(
            "shop", draft.topology_id, draft.version,
            nodes=valid_nodes(), edges=valid_edges(), viewport=draft.viewport,
            expected_digest=draft.content_digest,
        )
        validated = await service.validate("shop", saved.topology_id, saved.version)
        assert validated.status == TopologyStatus.VALIDATING
        active = await service.activate("shop", saved.topology_id, saved.version, "owner")
        assert active.status == TopologyStatus.ACTIVE
        restarted = TopologyService(store, project_registry(tmp_path), HealthyScheduler())
        assert (await restarted.current("shop")).nodes[2].position.x == 400
        assert await restarted.eligible_node_ids("shop", "coding") == {"exec-a", "exec-b"}
        assert await restarted.eligible_node_ids("shop", "test") == {"test-a"}
        revision = await restarted.create_draft("shop", "owner")
        assert revision.version == 2
        assert revision.nodes[2].position.x == 400

    asyncio.run(scenario())


def test_illegal_edge_is_rejected_and_invalid_version_cannot_activate(tmp_path):
    async def scenario():
        service = TopologyService(
            MemoryTopologyStore(), project_registry(tmp_path), HealthyScheduler()
        )
        draft = await service.create_draft("shop", "owner")
        illegal = valid_edges()
        illegal[1] = illegal[1].model_copy(
            update={"source": "test:test-a", "target": "project:shop"}
        )
        saved = await service.update(
            "shop", draft.topology_id, draft.version,
            nodes=valid_nodes(), edges=illegal, viewport=draft.viewport,
            expected_digest=draft.content_digest,
        )
        invalid = await service.validate("shop", saved.topology_id, saved.version)
        assert invalid.status == TopologyStatus.INVALID
        assert "illegal_edge" in {item.code for item in invalid.findings}
        with pytest.raises(TopologyConflictError, match="pass validation"):
            await service.activate("shop", invalid.topology_id, invalid.version, "owner")

    asyncio.run(scenario())


def test_topology_node_rejects_pool_and_resource_binding_together():
    with pytest.raises(ValueError, match="member_ids"):
        TopologyNode(
            node_id="pool:a",
            node_type=TopologyNodeType.RESOURCE_POOL,
            label="pool",
            resource_id="exec-a",
            member_ids=["exec-b"],
        )


def test_coding_router_passes_project_topology_allowlist(tmp_path):
    class Scheduler:
        async def run_coding(self, *args, **kwargs):
            assert kwargs["eligible_node_ids"] == {"exec-a", "exec-b"}
            return SimpleNamespace(
                node_id="exec-a", result=SimpleNamespace(usage={})
            )

    async def resolve(project_id, workload):
        assert (project_id, workload) == ("shop", "coding")
        return {"exec-a", "exec-b"}

    async def scenario():
        router = ScheduledCodingRouter(Scheduler(), resolve)
        result = await router.modify(
            "implement",
            SimpleNamespace(),
            str(tmp_path),
            task_context={"task_id": "task-a", "project_id": "shop"},
        )
        assert result.usage["coding_node"] == "exec-a"

    asyncio.run(scenario())
