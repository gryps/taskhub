import asyncio
from dataclasses import replace
from pathlib import Path

from taskhub_v2.api.remote_node_routes import router as remote_node_router
from taskhub_v2.domain.remote_nodes import RemoteNodeCreate
from taskhub_v2.persistence.configuration import MemoryConfigurationStore
from taskhub_v2.persistence.hosts import MemoryHostStore, new_host_record
from taskhub_v2.persistence.remote_nodes import MemoryRemoteNodeStore, new_remote_node_record
from taskhub_v2.services import remote_node_helpers as node_helpers
from taskhub_v2.services.image_distribution import image_sources
from taskhub_v2.services.remote_nodes import RemoteNodeError, RemoteNodeService


def test_upgrade_route_precedes_generic_action_route():
    paths = [route.path for route in remote_node_router.routes]
    assert paths.index("/api/remote-nodes/{node_id}/upgrade") < paths.index(
        "/api/remote-nodes/{node_id}/{action}"
    )


class FakeRegistry:
    def __init__(self):
        self.nodes = []
        self.exported = False

    def node_exists(self, _node_id):
        return False

    def register_node(self, node):
        self.nodes = [item for item in self.nodes if item.id != node.id]
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


class FakeCredentials:
    def __init__(self):
        self.values = {}
        self.versions = {}

    def issue(self, node_id):
        self.versions[node_id] = self.versions.get(node_id, 0) + 1
        value = f"unique-{node_id}-{self.versions[node_id]}"
        self.values[node_id] = value
        return value

    def ensure(self, node_id):
        return self.values.get(node_id) or self.issue(node_id)

    def resolve(self, node_id):
        return self.values.get(node_id, "")

    def rotate(self, node_id):
        self.versions[node_id] += 1
        self.values[node_id] = f"rotated-{node_id}-{self.versions[node_id]}"
        return self.values[node_id]

    def revoke(self, node_id):
        self.values.pop(node_id, None)

    def metadata(self, node_id):
        return {
            "status": "active" if node_id in self.values else "revoked",
            "version": self.versions.get(node_id, 0),
        }


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
        credentials=FakeCredentials(),
    )

    async def healthy(_address, _request, _token):
        return None

    service._wait_healthy = healthy
    return service, store, hosts, registry


class ReconcileHosts:
    def __init__(self):
        self.exists = False
        self.state = "missing"
        self.create_count = 0

    async def check(self, _host_id):
        return {"status": "available", "status_reason": "available"}

    async def run_remote_script(self, _host_id, script, timeout=120):
        if "printf 'EXISTS=0" in script:
            return (
                f"EXISTS={1 if self.exists else 0}\nSTATE={self.state}\n"
                f"CONTAINER={'container-123' if self.exists else ''}\n"
            )
        if "docker_run create" in script:
            self.exists = True
            self.state = "running"
            self.create_count += 1
            return "CONTAINER=container-123\nSTATE=running\n"
        if " start " in script:
            self.state = "running"
            return "STATE=running\n"
        if " stop " in script:
            self.state = "exited"
            return "STATE=exited\n"
        raise AssertionError((script, timeout))


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


def test_reconciliation_recreates_missing_container_once_and_registers_agent():
    async def scenario():
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
        store = MemoryRemoteNodeStore()
        await store.save(
            new_remote_node_record(
                node_id="worker-01",
                host_id="worker-host",
                payload={
                    "node_id": "worker-01",
                    "host_id": "worker-host",
                    "role": "execution",
                    "slots": 1,
                    "host_port": 8020,
                    "cpu_limit": "",
                    "memory_limit": "",
                    "image": "taskhub-node:0.1.0-alpha",
                },
                container_id="old-container",
                image_digest="sha256:image",
                desired_state="running",
                actual_state="offline",
                status_reason="Seed restarted",
            )
        )
        hosts = ReconcileHosts()
        registry = FakeRegistry()
        credentials = FakeCredentials()
        credentials.issue("worker-01")
        service = RemoteNodeService(
            store,
            hosts,
            host_store,
            MemoryConfigurationStore(),
            registry,
            image="taskhub-node:0.1.0-alpha",
            node_token="legacy",
            credentials=credentials,
        )

        async def healthy(_address, _request, token):
            assert token == credentials.resolve("worker-01")

        service._wait_healthy = healthy
        await service.reconcile_once()
        await service.reconcile_once()

        record = await store.get("worker-01")
        assert hosts.create_count == 1
        assert record.actual_state == "running"
        assert record.container_id == "container-123"
        assert record.payload["reconciliation"]["agent_last_seen_at"]
        assert [node.id for node in registry.nodes] == ["worker-01"]

        revoked = await service.revoke_credential("worker-01")
        assert revoked["credential"]["status"] == "revoked"
        assert revoked["desired_state"] == "stopped"
        assert registry.nodes == []

        restarted = await service.action("worker-01", "start")
        assert restarted["credential"]["status"] == "active"
        assert restarted["credential"]["version"] == 2
        assert restarted["actual_state"] == "running"
        assert [node.id for node in registry.nodes] == ["worker-01"]

    asyncio.run(scenario())


def test_reconciliation_removes_unreachable_agent_from_scheduling_then_recovers():
    async def scenario():
        service, store, _hosts, registry = await make_service(
            "FOUND=1\nSOURCE=private-registry\nIMAGE_ID=sha256:remote-image\n"
            "ARCH=amd64\nOS=linux\nDIGESTS=repo@sha256:resolved\n"
        )
        request = RemoteNodeCreate(
            node_id="worker-04",
            host_id="worker-host",
            role="execution",
            slots=1,
            host_port=8024,
        )
        await service.create(request)
        await asyncio.gather(*service._tasks.values())
        record = await store.get("worker-04")

        async def failing(_address, _request, _token):
            raise RemoteNodeError("network unavailable")

        service._wait_healthy = failing
        await service._mark_offline(record, "network unavailable")
        assert registry.nodes == []
        assert (await store.get("worker-04")).actual_state == "offline"

        async def healthy(_address, _request, _token):
            return None

        service._wait_healthy = healthy
        registry.register_node(node_helpers.node_definition("192.0.2.20", request))
        await service._save_reconciled(
            await store.get("worker-04"), "running", "recovered", seen=True
        )
        assert (await store.get("worker-04")).actual_state == "running"
        assert [node.id for node in registry.nodes] == ["worker-04"]

    asyncio.run(scenario())


def test_seed_restart_resumes_persisted_create_operation():
    async def scenario():
        service, store, _hosts, registry = await make_service(
            "FOUND=1\nSOURCE=configured-registry\nIMAGE_ID=sha256:resumed\n"
            "ARCH=amd64\nOS=linux\nDIGESTS=repo@sha256:resumed\n"
        )
        request = RemoteNodeCreate(
            node_id="worker-resume", host_id="worker-host", role="execution",
            slots=1, host_port=8030,
        )
        await store.save(new_remote_node_record(
            node_id=request.node_id, host_id=request.host_id,
            payload={
                **request.model_dump(mode="json"), "image": service.image,
                "cpu_limit": "", "memory_limit": "",
                "distribution": {"phase": "pulling", "percent": 15},
                "operation": {
                    "kind": "create", "status": "running", "phase": "pulling",
                    "percent": 15, "target_image": service.image,
                },
            },
            container_id="", image_digest="", desired_state="running",
            actual_state="distributing", status_reason="Seed restarted",
        ))
        await service.resume_pending_operations()
        await asyncio.gather(*service._tasks.values())

        record = await store.get(request.node_id)
        assert record.actual_state == "running"
        assert record.payload["operation"]["status"] == "complete"
        assert record.image_digest.endswith("@sha256:resumed")
        assert [item.id for item in registry.nodes] == [request.node_id]

    asyncio.run(scenario())


def test_remote_node_upgrade_commits_only_after_health_check():
    async def scenario():
        service, store, _hosts, _registry = await make_service(
            "FOUND=1\nSOURCE=private-registry\nIMAGE_ID=sha256:upgrade\n"
            "ARCH=amd64\nOS=linux\nDIGESTS=repo@sha256:upgrade\n"
        )
        await service.create(RemoteNodeCreate(
            node_id="worker-upgrade", host_id="worker-host", role="execution",
            slots=1, host_port=8031,
        ))
        await asyncio.gather(*service._tasks.values())
        queued = await service.upgrade("worker-upgrade", "taskhub-node:0.2.0")
        assert queued["operation"]["status"] == "queued"
        await asyncio.gather(*service._tasks.values())

        record = await store.get("worker-upgrade")
        assert record.actual_state == "running"
        assert record.payload["image"] == "taskhub-node:0.2.0"
        assert record.payload["operation"]["status"] == "complete"
        assert record.image_digest.endswith("@sha256:upgrade")

    asyncio.run(scenario())


def test_remote_node_upgrade_health_failure_rolls_back_old_image():
    async def scenario():
        service, store, _hosts, registry = await make_service(
            "FOUND=1\nSOURCE=private-registry\nIMAGE_ID=sha256:upgrade\n"
            "ARCH=amd64\nOS=linux\nDIGESTS=repo@sha256:upgrade\n"
        )
        await service.create(RemoteNodeCreate(
            node_id="worker-rollback", host_id="worker-host", role="execution",
            slots=1, host_port=8032,
        ))
        await asyncio.gather(*service._tasks.values())
        checks = 0

        async def fail_new_then_accept_old(_address, _request, _token):
            nonlocal checks
            checks += 1
            if checks == 1:
                raise RemoteNodeError("new agent unhealthy")

        service._wait_healthy = fail_new_then_accept_old
        await service.upgrade("worker-rollback", "taskhub-node:bad")
        await asyncio.gather(*service._tasks.values())

        record = await store.get("worker-rollback")
        assert record.actual_state == "running"
        assert record.payload["image"] == "taskhub-node:0.1.0-alpha"
        assert record.payload["operation"]["status"] == "rolled-back"
        assert "已自动恢复原镜像" in record.status_reason
        assert [item.id for item in registry.nodes] == ["worker-rollback"]

    asyncio.run(scenario())


def test_seed_restart_retries_persisted_upgrade_operation():
    async def scenario():
        service, store, _hosts, _registry = await make_service(
            "FOUND=1\nSOURCE=proxy\nIMAGE_ID=sha256:retry\n"
            "ARCH=amd64\nOS=linux\nDIGESTS=repo@sha256:retry\n"
        )
        await service.create(RemoteNodeCreate(
            node_id="worker-upgrade-resume", host_id="worker-host", role="execution",
            slots=1, host_port=8033,
        ))
        await asyncio.gather(*service._tasks.values())
        record = await store.get("worker-upgrade-resume")
        operation = {
            "id": "persisted-operation", "kind": "upgrade", "attempt": 1,
            "status": "running", "phase": "pulling", "percent": 15,
            "target_image": "taskhub-node:0.3.0",
            "previous_image": record.payload["image"],
            "previous_digest": record.image_digest,
        }
        await store.save(replace(
            record, payload={**record.payload, "operation": operation},
            actual_state="upgrading",
        ))

        await service.resume_pending_operations()
        await asyncio.gather(*service._tasks.values())
        resumed = await store.get("worker-upgrade-resume")
        assert resumed.payload["image"] == "taskhub-node:0.3.0"
        assert resumed.payload["operation"]["id"] == "persisted-operation"
        assert resumed.payload["operation"]["attempt"] == 2
        assert resumed.payload["operation"]["status"] == "complete"

    asyncio.run(scenario())
