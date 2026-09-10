import asyncio
from pathlib import Path

from taskhub_v2.domain.remote_nodes import RemoteNodeCreate
from taskhub_v2.persistence.configuration import MemoryConfigurationStore
from taskhub_v2.persistence.hosts import MemoryHostStore, new_host_record
from taskhub_v2.persistence.remote_nodes import MemoryRemoteNodeStore
from taskhub_v2.services.image_distribution import image_sources
from taskhub_v2.services.remote_nodes import RemoteNodeService


class FakeRegistry:
    def __init__(self):
        self.nodes = []
        self.exported = False

    def node_exists(self, _node_id):
        return False

    def register_node(self, node):
        self.nodes.append(node)

    def unregister_node(self, node_id):
        self.nodes = [node for node in self.nodes if node.id != node_id]

    def export_image(self, _image, destination):
        self.exported = True
        destination.write(b"image-tar")
        return {
            "id": "sha256:local-image",
            "digest": "sha256:local-image",
            "architecture": "amd64",
            "os": "linux",
        }


class FakeHosts:
    def __init__(self, pull_output):
        self.pull_output = pull_output
        self.scripts = []
        self.loaded = False

    async def run_remote_script(self, _host_id, script, timeout=120):
        self.scripts.append((script, timeout))
        if "docker_run pull" in script:
            return self.pull_output
        if "SOURCE=ssh-transfer" in script:
            return (
                "FOUND=1\nSOURCE=ssh-transfer\nIMAGE_ID=sha256:local-image\n"
                "ARCH=amd64\nOS=linux\nDIGESTS=\n"
            )
        return "CONTAINER=container-123\nSTATE=running\n"

    async def load_remote_image(self, _host_id, image_path: Path, timeout=1800):
        assert image_path.read_bytes() == b"image-tar"
        self.loaded = True
        return "Loaded image"


async def make_service(pull_output):
    host_store = MemoryHostStore()
    await host_store.save(
        new_host_record(
            host_id="worker-host",
            payload={
                "address": "192.0.2.20",
                "port": 22,
                "username": "taskhub",
                "docker_access": "direct",
                "allowed_roles": ["execution"],
            },
            encrypted_private_key="encrypted",
            host_key="host key",
            fingerprint="SHA256:test",
            status="available",
            facts={"architecture": "x86_64"},
            status_reason="available",
        )
    )
    registry = FakeRegistry()
    hosts = FakeHosts(pull_output)
    store = MemoryRemoteNodeStore()
    service = RemoteNodeService(
        store,
        hosts,
        host_store,
        MemoryConfigurationStore(),
        registry,
        image="taskhub-node:0.1.0-alpha",
        node_token="node-secret",
        image_registry="registry.example/team",
        image_proxy="mirror.example/docker.io",
        registry_username="puller",
        registry_password="registry-secret",
    )

    async def healthy(_address, _request):
        return None

    service._wait_healthy = healthy
    return service, store, hosts, registry


def test_remote_node_uses_registry_and_persists_completed_progress():
    async def scenario():
        service, store, hosts, registry = await make_service(
            "FOUND=1\nSOURCE=private-registry\n"
            "IMAGE_ID=sha256:remote-image\nARCH=amd64\nOS=linux\n"
            "DIGESTS=registry.example/team/taskhub-node@sha256:resolved\n"
        )
        request = RemoteNodeCreate(
            node_id="worker-01",
            host_id="worker-host",
            role="execution",
            slots=1,
            host_port=8020,
        )
        queued = await service.create(request)
        assert queued["distribution"]["phase"] == "queued"
        await asyncio.gather(*service._tasks.values())
        record = await store.get("worker-01")
        assert record.actual_state == "running"
        assert record.image_digest.endswith("@sha256:resolved")
        assert record.payload["distribution"] == {
            "phase": "complete",
            "percent": 100,
            "source": "private-registry",
            "detail": "镜像分发完成，Node Agent 健康且已加入调度",
            "digest": "registry.example/team/taskhub-node@sha256:resolved",
        }
        assert not registry.exported
        assert registry.nodes[0].id == "worker-01"
        assert "registry-secret" in hosts.scripts[0][0]

    asyncio.run(scenario())


def test_remote_node_falls_back_to_seed_ssh_transfer():
    async def scenario():
        service, store, hosts, registry = await make_service(
            "FOUND=0\nERROR=manifest unknown\n"
        )
        await service.create(
            RemoteNodeCreate(
                node_id="worker-02",
                host_id="worker-host",
                role="execution",
                slots=1,
                host_port=8021,
            )
        )
        await asyncio.gather(*service._tasks.values())
        record = await store.get("worker-02")
        assert registry.exported is True
        assert hosts.loaded is True
        assert record.actual_state == "running"
        assert record.image_digest == "sha256:local-image"
        assert record.payload["distribution"]["source"] == "ssh-transfer"

    asyncio.run(scenario())


def test_remote_node_rejects_incompatible_pulled_image_architecture():
    async def scenario():
        service, store, _hosts, registry = await make_service(
            "FOUND=1\nSOURCE=proxy\nIMAGE_ID=sha256:arm-image\n"
            "ARCH=arm64\nOS=linux\nDIGESTS=repo@sha256:arm\n"
        )
        await service.create(
            RemoteNodeCreate(
                node_id="worker-03",
                host_id="worker-host",
                role="execution",
                slots=1,
                host_port=8022,
            )
        )
        await asyncio.gather(*service._tasks.values())
        record = await store.get("worker-03")
        assert record.actual_state == "error"
        assert "不兼容" in record.status_reason
        assert registry.nodes == []

    asyncio.run(scenario())


def test_image_sources_prioritize_private_registry_then_proxy():
    assert image_sources(
        "taskhub-node:0.1.0-alpha", "registry.example/team", "mirror.example/docker.io"
    ) == [
        ("private-registry", "registry.example/team/taskhub-node:0.1.0-alpha"),
        ("proxy", "mirror.example/docker.io/taskhub-node:0.1.0-alpha"),
        ("configured-registry", "taskhub-node:0.1.0-alpha"),
    ]
