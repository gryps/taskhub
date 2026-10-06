from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import time
from importlib.resources import files
from pathlib import Path
from threading import RLock

import httpx

from taskhub_v2.domain.external_windows import WindowsNodeConnection, WindowsNodeInstall
from taskhub_v2.domain.models import NodeDefinition
from taskhub_v2.security.node_credentials import NodeCredentialError
from taskhub_v2.services.host_connection import scan_host_key


class ExternalWindowsNodeError(RuntimeError):
    pass


class ExternalWindowsNodeService:
    """Own native Windows test-node admission without reviving a general host pool."""

    def __init__(self, state_file: str, registry, credentials, operation_log=None):
        self.path = Path(state_file)
        self.registry = registry
        self.credentials = credentials
        self.operation_log = operation_log
        self.lock = RLock()

    async def probe(self, request: WindowsNodeConnection) -> dict:
        return await _in_thread(self._probe, request)

    async def list(self) -> dict:
        records = self._read()
        nodes = {item.id: item for item in self.registry.list()}
        items = []
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            for record in records:
                node = nodes.get(record["node_id"])
                health = {"status": "not_registered", "detail": "Agent 尚未注册"}
                if node:
                    try:
                        response = await client.get(
                            f"{node.url.rstrip('/')}/api/health",
                            headers={
                                "Authorization": f"Bearer {self.credentials.resolve(node.id)}"
                            },
                        )
                        response.raise_for_status()
                        health = response.json()
                    except Exception as exc:
                        health = {"status": "unavailable", "detail": _safe_error(exc)}
                items.append({**record, "health": health})
        return {"nodes": items}

    async def install(self, request: WindowsNodeInstall) -> dict:
        if not request.expected_fingerprint:
            raise ExternalWindowsNodeError("请先检测并确认 SSH 主机指纹")
        probe = await self.probe(request)
        if probe["status"] != "available":
            raise ExternalWindowsNodeError(probe["detail"])
        credential_created = False
        try:
            metadata = self.credentials.metadata(request.node_id)
            if metadata["status"] == "active":
                token = self.credentials.resolve(request.node_id)
            else:
                token = self.credentials.issue(request.node_id)
                credential_created = True
        except NodeCredentialError as exc:
            raise ExternalWindowsNodeError(str(exc)) from exc
        try:
            await _in_thread(self._install_remote, request, probe["host_key"], token)
            health = await self._wait_healthy(request, token)
            workloads = {"test", "acceptance"}
            if request.browser_mode:
                workloads.add("browser_acceptance")
            self.registry.upsert(
                NodeDefinition(
                    id=request.node_id,
                    kind="remote",
                    url=f"http://{request.address}:{request.agent_port}",
                    slots=request.slots,
                    workloads=workloads,
                    priority=50,
                )
            )
            record = {
                "node_id": request.node_id,
                "display_name": request.display_name,
                "address": request.address,
                "ssh_port": request.ssh_port,
                "username": request.username,
                "agent_port": request.agent_port,
                "slots": request.slots,
                "browser_mode": request.browser_mode,
                "fingerprint": request.expected_fingerprint,
                "installed_at": _now(),
            }
            self._save(record)
            self._audit("external_windows_install", "passed", request.node_id)
            return {**record, "health": health}
        except Exception as exc:
            if credential_created:
                self.credentials.revoke(request.node_id)
                self.registry.remove(request.node_id)
            self._audit("external_windows_install", "failed", request.node_id, exc)
            raise ExternalWindowsNodeError(_safe_error(exc)) from exc

    async def remove(self, node_id: str) -> dict:
        if not any(item["node_id"] == node_id for item in self._read()):
            raise KeyError(node_id)
        self.registry.remove(node_id)
        self.credentials.revoke(node_id)
        self._write([item for item in self._read() if item["node_id"] != node_id])
        self._audit("external_windows_remove", "passed", node_id)
        return {"node_id": node_id, "removed": True, "remote_agent_retained": True}

    def _probe(self, request: WindowsNodeConnection) -> dict:
        try:
            host_key, fingerprint = scan_host_key(request.address, request.ssh_port)
        except Exception as exc:
            raise ExternalWindowsNodeError(_safe_error(exc)) from exc
        if not request.expected_fingerprint:
            return {
                "status": "confirmation_required",
                "detail": "请从可信渠道核对并确认 Windows 主机指纹",
                "fingerprint": fingerprint,
                "host_key": host_key,
                "facts": {},
            }
        if fingerprint != request.expected_fingerprint:
            raise ExternalWindowsNodeError(
                f"SSH 主机指纹不一致；期望 {request.expected_fingerprint}，实际 {fingerprint}"
            )
        private_key = request.private_key.get_secret_value().strip()
        if "PRIVATE KEY-----" not in private_key or len(private_key) > 32768:
            raise ExternalWindowsNodeError("SSH 私钥格式无效或文件过大")
        script = r"""
$ErrorActionPreference='Stop'
$python=(Get-Command python -ErrorAction SilentlyContinue).Source
if(-not $python){$python=(Get-Command py -ErrorAction SilentlyContinue).Source}
if(-not $python){throw 'Python 3.12 or later is required'}
$os=Get-CimInstance Win32_OperatingSystem
$disk=Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
[pscustomobject]@{os=$os.Caption;version=$os.Version;architecture=$os.OSArchitecture;
python=$python;cpu=[Environment]::ProcessorCount;memory_bytes=[int64]$os.TotalVisibleMemorySize*1024;
disk_free_bytes=[int64]$disk.FreeSpace} | ConvertTo-Json -Compress
"""
        result = _run_windows_ssh(request, private_key, host_key, script, 30)
        facts = json.loads(_last_json_line(result.stdout))
        return {
            "status": "available",
            "detail": "SSH、Windows 与 Python 准入检测通过",
            "fingerprint": fingerprint,
            "host_key": host_key,
            "facts": facts,
        }

    def _install_remote(
        self, request: WindowsNodeInstall, host_key: str, token: str
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="taskhub-windows-node-") as directory:
            subprocess.run(
                [
                    sys.executable, "-m", "pip", "wheel", "--no-deps",
                    "--no-build-isolation", "--wheel-dir", directory, str(Path.cwd()),
                ],
                capture_output=True, text=True, timeout=180, check=True,
            )
            wheel = next(Path(directory).glob("taskhub_v2-*.whl"))
            installer = files("taskhub_v2").joinpath("assets/install-windows-node-agent.ps1")
            installer_path = Path(directory, "install.ps1")
            installer_path.write_bytes(installer.read_bytes())
            private_key = request.private_key.get_secret_value().strip()
            _run_windows_ssh(
                request,
                private_key,
                host_key,
                "$root='C:\\TaskHub\\incoming';"
                "New-Item -ItemType Directory -Force -Path $root | Out-Null",
                30,
            )
            _copy_windows_files(
                request, private_key, host_key, (wheel, installer_path)
            )
            script = _deployment_script(request, token, wheel.name)
            _run_windows_ssh(
                request,
                private_key,
                host_key,
                script,
                1200,
            )

    async def _wait_healthy(self, request: WindowsNodeInstall, token: str) -> dict:
        url = f"http://{request.address}:{request.agent_port}/api/health"
        deadline = time.monotonic() + 90
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            while time.monotonic() < deadline:
                try:
                    response = await client.get(
                        url, headers={"Authorization": f"Bearer {token}"}
                    )
                    response.raise_for_status()
                    health = response.json()
                    if health.get("node_id") != request.node_id:
                        raise ExternalWindowsNodeError("Agent 返回的节点 ID 与登记值不一致")
                    return health
                except ExternalWindowsNodeError:
                    raise
                except Exception:
                    await _sleep(2)
        raise ExternalWindowsNodeError("Agent 已安装，但 Seed 在 90 秒内无法访问其健康端点")

    def _read(self) -> list[dict]:
        with self.lock:
            if not self.path.is_file():
                return []
            try:
                return list(json.loads(self.path.read_text(encoding="utf-8")).get("nodes", []))
            except (OSError, ValueError, TypeError) as exc:
                raise ExternalWindowsNodeError("外部 Windows 节点清单损坏或不可读") from exc

    def _save(self, record: dict) -> None:
        records = [item for item in self._read() if item["node_id"] != record["node_id"]]
        self._write([*records, record])

    def _write(self, records: list[dict]) -> None:
        with self.lock:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps({"nodes": records}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.chmod(temporary, 0o600)
            temporary.replace(self.path)

    def _audit(self, action: str, result: str, node_id: str, error=None) -> None:
        if self.operation_log:
            self.operation_log.record(
                action, result, node_id=node_id, error=_safe_error(error) if error else ""
            )


def _deployment_script(request, token: str, wheel_name: str) -> str:
    browser = "$true" if request.browser_mode else "$false"
    node_id = _powershell_quote(request.node_id)
    browser_target = _powershell_quote(request.browser_auth_target)
    credential = _powershell_quote(token)
    package_name = _powershell_quote(wheel_name)
    return f"""
$ErrorActionPreference='Stop'
$root='C:\\TaskHub\\incoming'
$env:TASKHUB_NODE_TOKEN='{credential}'
$python=(Get-Command python -ErrorAction SilentlyContinue).Source
if(-not $python){{$python=(Get-Command py -ErrorAction SilentlyContinue).Source}}
& (Join-Path $root 'install.ps1') -Python $python `
  -PackagePath (Join-Path $root '{package_name}') `
  -NodeId '{node_id}' -Port {request.agent_port} -Slots {request.slots} `
  -BrowserMode:{browser} -BrowserAuthTarget '{browser_target}'
if($LASTEXITCODE -ne 0){{throw 'TaskHub Windows Agent installer failed'}}
"""


def _copy_windows_files(request, private_key: str, host_key: str, sources) -> None:
    with tempfile.TemporaryDirectory(prefix="taskhub-windows-scp-") as directory:
        key = Path(directory, "identity")
        known_hosts = Path(directory, "known_hosts")
        key.write_text(private_key + "\n", encoding="utf-8")
        key.chmod(0o600)
        known_hosts.write_text(host_key + "\n", encoding="utf-8")
        common = [
            "-i", str(key), "-P", str(request.ssh_port), "-q",
            "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
            "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={known_hosts}",
        ]
        for source in sources:
            destination = (
                f"{request.username}@{request.address}:"
                f"C:/TaskHub/incoming/{source.name}"
            )
            try:
                result = subprocess.run(
                    ["scp", *common, str(source), destination],
                    capture_output=True, text=True, timeout=180,
                    env={**os.environ, "LC_ALL": "C"}, check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise ExternalWindowsNodeError(
                    f"SCP 安装文件传输失败：{_safe_error(exc)}"
                ) from exc
            if result.returncode:
                raise ExternalWindowsNodeError(
                    f"SCP 安装文件传输失败：{_safe_error(result.stderr)}"
                )


def _powershell_quote(value: str) -> str:
    return value.replace("'", "''")


def _run_windows_ssh(request, private_key: str, host_key: str, script: str, timeout: int):
    with tempfile.TemporaryDirectory(prefix="taskhub-windows-ssh-") as directory:
        key = Path(directory, "identity")
        known_hosts = Path(directory, "known_hosts")
        key.write_text(private_key + "\n", encoding="utf-8")
        key.chmod(0o600)
        known_hosts.write_text(host_key + "\n", encoding="utf-8")
        command = [
            "ssh", "-i", str(key), "-p", str(request.ssh_port), "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=8", "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={known_hosts}",
            f"{request.username}@{request.address}",
            "powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-EncodedCommand", _powershell_stdin_bootstrap(),
        ]
        try:
            result = subprocess.run(
                command, input=script, capture_output=True, text=True,
                timeout=timeout, env={**os.environ, "LC_ALL": "C"}, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ExternalWindowsNodeError(f"SSH 远程操作失败：{_safe_error(exc)}") from exc
        if result.returncode:
            raise ExternalWindowsNodeError(f"SSH 远程操作失败：{_safe_error(result.stderr)}")
        return result


def _powershell_stdin_bootstrap() -> str:
    """Execute stdin as one script block on Windows PowerShell 5.1 and newer.

    ``-Command -`` parses piped input incrementally on Windows PowerShell 5.1.
    Multi-line constructs can consequently finish with exit code zero without
    ever executing the complete probe or installer.  The small encoded
    bootstrap has no command-shell metacharacters and evaluates stdin only
    after it has been read in full.
    """
    bootstrap = (
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);"
        "$OutputEncoding=[Console]::OutputEncoding;"
        "$source=[Console]::In.ReadToEnd();"
        "& ([ScriptBlock]::Create($source))"
    )
    return base64.b64encode(bootstrap.encode("utf-16-le")).decode("ascii")


def _last_json_line(output: str) -> str:
    line = next(
        (item for item in reversed(output.splitlines()) if item.strip().startswith("{")),
        "",
    )
    if not line:
        raise ExternalWindowsNodeError("Windows 主机未返回有效的准入信息")
    return line


def _safe_error(error) -> str:
    import re

    value = str(error or "")
    value = re.sub(r"(?i)(authorization|password|private.?key|token|secret)[^\s;]*", "凭据", value)
    return " ".join(value.split())[:500] or "远程操作失败"


async def _in_thread(function, *args):
    import asyncio

    return await asyncio.to_thread(function, *args)


async def _sleep(seconds: int):
    import asyncio

    await asyncio.sleep(seconds)


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()
