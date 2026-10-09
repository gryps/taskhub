import asyncio
import base64
import json

from taskhub_v2.domain.external_windows import WindowsNodeConnection, WindowsNodeInstall
from taskhub_v2.execution.registry import NodeRegistry
from taskhub_v2.services.external_windows_nodes import (
    ExternalWindowsNodeService,
    _deployment_script,
    _powershell_stdin_bootstrap,
)


class Credentials:
    def __init__(self):
        self.values = {}
        self.revoked = set()

    def metadata(self, node_id):
        return {"status": "active" if node_id in self.values else "missing"}

    def issue(self, node_id):
        self.values[node_id] = f"secret-{node_id}"
        return self.values[node_id]

    def rotate(self, node_id):
        self.values[node_id] = f"rotated-{node_id}"
        return self.values[node_id]

    def resolve(self, node_id):
        return self.values.get(node_id, "")

    def revoke(self, node_id):
        self.values.pop(node_id, None)
        self.revoked.add(node_id)


def request(**updates):
    values = {
        "node_id": "windows-test-34",
        "display_name": "Windows 实测机",
        "address": "192.168.31.34",
        "ssh_port": 22,
        "username": "user",
        "private_key": "-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----",
        "expected_fingerprint": "SHA256:abcdefghijklmnopqrstuvwxyz123456",
        "agent_port": 8301,
        "slots": 1,
        "browser_mode": False,
    }
    values.update(updates)
    return WindowsNodeInstall(**values)


def test_probe_requires_explicit_host_fingerprint(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "taskhub_v2.services.external_windows_nodes.scan_host_key",
        lambda address, port: ("host key", "SHA256:abcdefghijklmnopqrstuvwxyz123456"),
    )
    service = ExternalWindowsNodeService(
        str(tmp_path / "external.json"),
        NodeRegistry(str(tmp_path / "nodes.json")),
        Credentials(),
    )
    result = asyncio.run(
        service.probe(
            WindowsNodeConnection(
                address="192.168.31.34",
                username="user",
                private_key="not-used-before-confirmation",
            )
        )
    )
    assert result["status"] == "confirmation_required"
    assert result["fingerprint"].startswith("SHA256:")


def test_install_registers_native_node_without_persisting_ssh_key(monkeypatch, tmp_path):
    credentials = Credentials()
    registry = NodeRegistry(str(tmp_path / "nodes.json"))
    service = ExternalWindowsNodeService(
        str(tmp_path / "external.json"), registry, credentials
    )

    async def probe(_request):
        return {"status": "available", "detail": "ok", "host_key": "host key"}

    async def healthy(_request, _token):
        return {"status": "ok", "node_id": "windows-test-34", "capabilities": {}}

    monkeypatch.setattr(service, "probe", probe)
    monkeypatch.setattr(service, "_install_remote", lambda *_args: None)
    monkeypatch.setattr(service, "_wait_healthy", healthy)

    result = asyncio.run(service.install(request()))

    assert result["health"]["status"] == "ok"
    node = registry.list()[0]
    assert node.id == "windows-test-34"
    assert node.url == "http://192.168.31.34:8301"
    assert node.workloads == {"test", "acceptance"}
    stored = (tmp_path / "external.json").read_text(encoding="utf-8")
    assert "PRIVATE KEY" not in stored
    assert "secret-windows-test-34" not in stored


def test_browser_node_registers_browser_workload_and_remove_revokes(tmp_path, monkeypatch):
    credentials = Credentials()
    registry = NodeRegistry(str(tmp_path / "nodes.json"))
    service = ExternalWindowsNodeService(
        str(tmp_path / "external.json"), registry, credentials
    )

    async def probe(_request):
        return {"status": "available", "detail": "ok", "host_key": "host key"}

    async def healthy(_request, _token):
        return {"status": "ok", "node_id": "windows-test-34", "capabilities": {}}

    monkeypatch.setattr(service, "probe", probe)
    monkeypatch.setattr(service, "_install_remote", lambda *_args: None)
    monkeypatch.setattr(service, "_wait_healthy", healthy)
    asyncio.run(
        service.install(
            request(browser_mode=True, browser_auth_target="https://example.test/login")
        )
    )
    assert "browser_acceptance" in registry.list()[0].workloads

    removed = asyncio.run(service.remove("windows-test-34"))
    assert removed["remote_agent_retained"] is True
    assert registry.list() == []
    assert "windows-test-34" in credentials.revoked
    assert json.loads((tmp_path / "external.json").read_text())["nodes"] == []


def test_windows_installer_uses_persistent_cache_and_no_plaintext_token():
    script = (
        __import__("pathlib").Path("src/taskhub_v2/assets/install-windows-node-agent.ps1")
        .read_text(encoding="utf-8")
    )
    assert "cache\\pip" in script
    assert ".taskhub-dependencies-v1" in script
    assert "if (-not (Test-Path $DependencyMarker))" in script
    assert "ProtectedData" in script
    assert "node-token.dpapi" in script
    assert "TaskHubNodeAgent" in script
    assert "Invoke-RestMethod -Uri $HealthUri" in script
    assert "$Health.node_id -eq $NodeId" in script
    assert "Start-Process -FilePath \"powershell.exe\"" not in script
    assert "Stop-ScheduledTask -TaskName $TaskName" in script
    assert script.index("Stop-ScheduledTask") < script.index("Register-ScheduledTask")


def test_windows_ssh_bootstrap_reads_the_complete_script_before_execution():
    bootstrap = base64.b64decode(_powershell_stdin_bootstrap()).decode("utf-16-le")

    assert "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)" in bootstrap
    assert "[Console]::In.ReadToEnd()" in bootstrap
    assert "[ScriptBlock]::Create($source)" in bootstrap


def test_windows_deployment_script_uses_transferred_assets_not_embedded_wheel():
    script = _deployment_script(
        request(), "secret-node-token", "taskhub_v2-0.1.0-py3-none-any.whl"
    )

    assert "C:\\TaskHub\\incoming" in script
    assert "taskhub_v2-0.1.0-py3-none-any.whl" in script
    assert "install.ps1" in script
    assert "FromBase64String" not in script
