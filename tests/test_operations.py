import asyncio
import io
import json
import zipfile
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from taskhub_v2.api.app import create_app
from taskhub_v2.config import Settings
from taskhub_v2.persistence.configuration import MemoryConfigurationStore
from taskhub_v2.persistence.hosts import MemoryHostStore, new_host_record
from taskhub_v2.services.hosts import PhysicalHostService
from taskhub_v2.services.operational_log import OperationalLog, redact_text, sanitize
from taskhub_v2.services.system_diagnostics import SystemDiagnosticsService


def test_operational_log_redacts_secrets_and_diagnostic_addresses(tmp_path):
    journal = OperationalLog(str(tmp_path / "operations.jsonl"))
    journal.record(
        "ssh_docker", "failed", host_id="worker-1", node_id="node-1",
        error="Authorization: Bearer-secret password=plain 192.168.31.60",
    )
    event = journal.list(node_id="node-1")[0]
    assert "plain" not in event["error"]
    assert "Bearer-secret" not in event["error"]
    exported = sanitize(event, addresses=True)
    assert "192.168.31.60" not in json.dumps(exported)
    assert "[REDACTED-IP]" in json.dumps(exported)
    assert redact_text("api_key=abcdef123456") == "api_key=[REDACTED]"
    assert "bearer-value" not in redact_text("Authorization: Bearer bearer-value")
    assert "database-pass" not in redact_text(
        "postgresql://taskhub:database-pass@postgres/taskhub"
    )


def test_backup_identity_rejects_changed_key():
    async def scenario():
        store = MemoryConfigurationStore()
        await store.ensure_backup_identity("first")
        await store.ensure_backup_identity("first")
        with pytest.raises(RuntimeError, match="主密钥"):
            await store.ensure_backup_identity("second")

    asyncio.run(scenario())


def test_host_maintenance_state_and_resource_alerts(tmp_path):
    async def scenario():
        store = MemoryHostStore()
        await store.save(
            new_host_record(
                host_id="worker-host",
                payload={"display_name": "Worker", "operational_state": "active"},
                encrypted_private_key="encrypted", host_key="key",
                fingerprint="SHA256:test",
                status="available",
                facts={"memory_bytes": 100, "memory_available_bytes": 5,
                       "disk_total_bytes": 100, "disk_available_bytes": 5},
                status_reason="available",
            )
        )
        service = PhysicalHostService(
            store, MemoryConfigurationStore(), None, "http://seed:8200",
            operation_log=OperationalLog(str(tmp_path / "operations.jsonl")),
        )
        result = await service.set_operational_state("worker-host", "draining")
        assert result["status"] == "draining"
        assert result["operational_state"] == "draining"
        assert len(result["alerts"]) == 2
        assert service.operation_log.list(host_id="worker-host")[0]["state"] == "draining"

    asyncio.run(scenario())


def test_diagnostic_export_is_readable_and_redacts_addresses(tmp_path):
    class Hosts:
        async def list(self):
            return {"hosts": [{"address": "192.168.31.60", "private_key": "secret"}]}

    class RemoteNodes:
        async def list(self):
            return {"nodes": []}

    class Scheduler:
        async def status(self):
            return []

    class Containers:
        def list(self):
            return []

    async def scenario():
        service = SystemDiagnosticsService(
            SimpleNamespace(env="seed", checkpointer="postgres"), Hosts(), RemoteNodes(),
            Containers(), Scheduler(), OperationalLog(str(tmp_path / "operations.jsonl")),
        )
        content, digest = await service.export()
        assert len(digest) == 64
        with zipfile.ZipFile(io.BytesIO(content)) as bundle:
            assert json.loads(bundle.read("manifest.json"))["format"].endswith("diagnostics-v1")
            hosts = bundle.read("hosts.json").decode()
            assert "192.168.31.60" not in hosts
            assert "secret" not in hosts

    asyncio.run(scenario())


def test_diagnostic_export_endpoint_is_authenticated_and_downloadable(tmp_path):
    settings = Settings(
        checkpointer="memory", admin_token="diagnostic-secret",
        session_secret="diagnostic-session",
        operations_log_file=str(tmp_path / "operations.jsonl"),
        nodes_file=str(tmp_path / "nodes.json"),
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/diagnostics/export").status_code == 401
        assert client.post(
            "/api/auth/login", json={"token": "diagnostic-secret"}
        ).status_code == 200
        response = client.get("/api/diagnostics/export")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        assert len(response.headers["x-taskhub-sha256"]) == 64
        with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
            assert "operations.json" in bundle.namelist()
