from __future__ import annotations

import asyncio
import os
import re
import shlex
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from taskhub_v2.domain.hosts import HostConnection, PhysicalHostCreate
from taskhub_v2.persistence.hosts import PhysicalHostRecord, new_host_record
from taskhub_v2.security import SecretCipher


class HostAdmissionError(RuntimeError):
    pass


PROBE_SCRIPT = r"""
set -eu
docker_access="$1"
callback="$2"
docker_run() {
  if [ "$docker_access" = "sudo" ]; then
    sudo -n docker "$@"
  else
    docker "$@"
  fi
}
os_value=$(uname -srm)
arch_value=$(uname -m)
cpu_value=$(getconf _NPROCESSORS_ONLN 2>/dev/null || nproc)
memory_value=$(awk '/MemTotal:/ {print $2 * 1024}' /proc/meminfo)
disk_value=$(df -Pk / | awk 'NR == 2 {print $4 * 1024}')
docker_value=$(docker_run version --format '{{.Server.Version}}')
callback_value=failed
if command -v curl >/dev/null 2>&1; then
  curl -fsS --max-time 6 "${callback%/}/api/health" >/dev/null && callback_value=passed
elif command -v wget >/dev/null 2>&1; then
  wget -q -T 6 -O /dev/null "${callback%/}/api/health" && callback_value=passed
fi
printf 'OS=%s\nARCH=%s\nCPU=%s\nMEMORY=%s\nDISK=%s\nDOCKER=%s\nCALLBACK=%s\n' \
  "$os_value" "$arch_value" "$cpu_value" "$memory_value" "$disk_value" \
  "$docker_value" "$callback_value"
"""


class PhysicalHostService:
    def __init__(self, store, configuration_store, cipher: SecretCipher | None, callback: str):
        self.store = store
        self.configuration_store = configuration_store
        self.cipher = cipher
        self.callback = callback

    async def list(self) -> dict[str, Any]:
        return {"hosts": [_host_view(item) for item in await self.store.list()]}

    async def probe(self, request: HostConnection) -> dict[str, Any]:
        return await asyncio.to_thread(self._probe, request)

    async def save(self, request: PhysicalHostCreate) -> dict[str, Any]:
        if not self.cipher:
            raise HostAdmissionError("尚未配置主机凭据加密根密钥")
        outcome = await self.probe(request)
        if outcome["status"] != "available":
            raise HostAdmissionError(outcome["detail"])
        existing = await self.store.get(request.host_id)
        payload = request.model_dump(exclude={"private_key", "expected_fingerprint"}, mode="json")
        record = new_host_record(
            host_id=request.host_id,
            payload=payload,
            encrypted_private_key=self.cipher.encrypt(request.private_key.get_secret_value()),
            host_key=outcome["host_key"],
            fingerprint=outcome["fingerprint"],
            status="available",
            facts=outcome["facts"],
            status_reason="准入检测通过",
        )
        if existing:
            record = replace(record, created_at=existing.created_at)
        saved = await self.store.save(record)
        await self._audit("admit", request.host_id, "passed")
        return _host_view(saved)

    async def check(self, host_id: str) -> dict[str, Any]:
        record = await self.store.get(host_id)
        if not record:
            raise KeyError(host_id)
        if not self.cipher:
            raise HostAdmissionError("主机凭据加密根密钥不可用")
        request = HostConnection(
            **record.payload,
            private_key=self.cipher.decrypt(record.encrypted_private_key),
            expected_fingerprint=record.fingerprint,
        )
        try:
            outcome = await self.probe(request)
            checked_at = _now()
            updated = replace(
                record,
                host_key=outcome["host_key"],
                facts=outcome["facts"],
                status="available",
                status_reason="重新检测通过",
                updated_at=checked_at,
                last_checked_at=checked_at,
            )
            result = "passed"
        except HostAdmissionError as exc:
            updated = replace(
                record,
                status="degraded",
                status_reason=_safe_error(exc),
                last_checked_at=_now(),
            )
            result = "failed"
        saved = await self.store.save(updated)
        await self._audit("check", host_id, result)
        return _host_view(saved)

    async def run_remote_script(self, host_id: str, script: str, timeout: int = 120) -> str:
        record = await self.store.get(host_id)
        if not record:
            raise KeyError(host_id)
        if record.status != "available":
            raise HostAdmissionError(f"物理主机 {host_id} 当前不可用：{record.status_reason}")
        if not self.cipher:
            raise HostAdmissionError("主机凭据加密根密钥不可用")
        return await asyncio.to_thread(
            _run_remote_script,
            record,
            self.cipher.decrypt(record.encrypted_private_key),
            script,
            timeout,
        )

    def _probe(self, request: HostConnection) -> dict[str, Any]:
        _require_ssh_tools()
        host_key, fingerprint = _scan_host_key(request.address, request.port)
        if not request.expected_fingerprint:
            return {
                "status": "confirmation_required",
                "detail": "请从可信渠道核对并确认主机指纹",
                "fingerprint": fingerprint,
                "host_key": host_key,
                "facts": {},
                "checks": [{"name": "SSH 主机指纹", "status": "confirm"}],
            }
        if request.expected_fingerprint != fingerprint:
            raise HostAdmissionError(
                f"SSH 主机指纹不一致；期望 {request.expected_fingerprint}，实际 {fingerprint}"
            )
        if not self.callback.startswith(("http://", "https://")):
            raise HostAdmissionError("请先在平台设置中配置远程主机可访问的 Node Agent 回连地址")
        private_key = request.private_key.get_secret_value().strip()
        if "PRIVATE KEY-----" not in private_key or len(private_key) > 32768:
            raise HostAdmissionError("SSH 私钥格式无效或文件过大")
        facts = _run_remote_probe(request, private_key, host_key, self.callback)
        if facts["callback"] != "passed":
            raise HostAdmissionError(
                f"远程主机无法访问 Node Agent 回连地址 {self.callback}/api/health"
            )
        return {
            "status": "available",
            "detail": "SSH、Docker、硬件资源和 Node Agent 回连检测通过",
            "fingerprint": fingerprint,
            "host_key": host_key,
            "facts": facts,
            "checks": [
                {"name": "SSH 免密认证", "status": "pass"},
                {"name": "Docker Engine", "status": "pass"},
                {"name": "Node Agent 回连", "status": "pass"},
            ],
        }

    async def _audit(self, action: str, host_id: str, result: str) -> None:
        await self.configuration_store.add_audit(
            operator="admin",
            scope="physical_hosts",
            action=action,
            parameter_summary={"host_id": host_id},
            result=result,
        )


def _require_ssh_tools() -> None:
    for executable in ("ssh", "ssh-keyscan", "ssh-keygen"):
        if not _which(executable):
            raise HostAdmissionError(f"Seed 镜像缺少 {executable}，请升级控制节点镜像")


def _scan_host_key(address: str, port: int) -> tuple[str, str]:
    try:
        result = subprocess.run(
            ["ssh-keyscan", "-T", "7", "-p", str(port), "-t", "ed25519,rsa,ecdsa", address],
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "LC_ALL": "C"},
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HostAdmissionError(f"无法读取 SSH 主机指纹：{_safe_error(exc)}") from exc
    lines = [line for line in result.stdout.splitlines() if line and not line.startswith("#")]
    if not lines:
        raise HostAdmissionError("SSH 端口不可达，未读取到主机指纹")
    host_key = next((line for line in lines if " ssh-ed25519 " in line), lines[0])
    fingerprint_result = subprocess.run(
        ["ssh-keygen", "-lf", "-", "-E", "sha256"],
        input=host_key + "\n",
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    match = re.search(r"SHA256:[A-Za-z0-9+/]+", fingerprint_result.stdout)
    if fingerprint_result.returncode or not match:
        raise HostAdmissionError("无法计算 SSH 主机指纹")
    return host_key, match.group(0)


def _run_remote_probe(
    request: HostConnection, private_key: str, host_key: str, callback: str
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="taskhub-host-") as directory:
        key_path = Path(directory, "identity")
        known_hosts_path = Path(directory, "known_hosts")
        key_path.write_text(private_key + "\n", encoding="utf-8")
        key_path.chmod(0o600)
        known_hosts_path.write_text(host_key + "\n", encoding="utf-8")
        command = [
            "ssh",
            "-i",
            str(key_path),
            "-p",
            str(request.port),
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=8",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={known_hosts_path}",
            f"{request.username}@{request.address}",
            "sh",
            "-s",
            "--",
            shlex.quote(request.docker_access),
            shlex.quote(callback),
        ]
        try:
            result = subprocess.run(
                command,
                input=PROBE_SCRIPT,
                capture_output=True,
                text=True,
                timeout=25,
                env={**os.environ, "LC_ALL": "C"},
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HostAdmissionError(f"SSH 准入检测失败：{_safe_error(exc)}") from exc
    if result.returncode:
        raise HostAdmissionError(f"SSH 准入检测失败：{_safe_text(result.stderr)}")
    values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    required = {"OS", "ARCH", "CPU", "MEMORY", "DISK", "DOCKER", "CALLBACK"}
    if not required.issubset(values):
        raise HostAdmissionError("远程主机返回的准入信息不完整")
    return {
        "os": values["OS"],
        "architecture": values["ARCH"],
        "cpu_count": int(float(values["CPU"])),
        "memory_bytes": int(float(values["MEMORY"])),
        "disk_available_bytes": int(float(values["DISK"])),
        "docker_version": values["DOCKER"],
        "callback": values["CALLBACK"],
    }


def _run_remote_script(
    record: PhysicalHostRecord, private_key: str, script: str, timeout: int
) -> str:
    payload = record.payload
    with tempfile.TemporaryDirectory(prefix="taskhub-host-") as directory:
        key_path = Path(directory, "identity")
        known_hosts_path = Path(directory, "known_hosts")
        key_path.write_text(private_key.strip() + "\n", encoding="utf-8")
        key_path.chmod(0o600)
        known_hosts_path.write_text(record.host_key + "\n", encoding="utf-8")
        command = [
            "ssh",
            "-i",
            str(key_path),
            "-p",
            str(payload["port"]),
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=8",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={known_hosts_path}",
            f"{payload['username']}@{payload['address']}",
            "sh",
            "-s",
        ]
        try:
            result = subprocess.run(
                command,
                input=script,
                capture_output=True,
                text=True,
                timeout=timeout,
                env={**os.environ, "LC_ALL": "C"},
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HostAdmissionError(f"SSH 远程操作失败：{_safe_error(exc)}") from exc
    if result.returncode:
        raise HostAdmissionError(f"SSH 远程操作失败：{_safe_text(result.stderr)}")
    return result.stdout


def _host_view(record: PhysicalHostRecord) -> dict[str, Any]:
    labels = {"available": "可用", "degraded": "异常", "unreachable": "不可达"}
    payload = dict(record.payload)
    # Records admitted by the earlier Alpha used a free-text purpose field.
    payload.pop("purpose", None)
    payload.setdefault("allowed_roles", ["execution", "test", "preproduction"])
    return {
        **payload,
        "fingerprint": record.fingerprint,
        "status": record.status,
        "status_label": labels.get(record.status, record.status),
        "facts": record.facts,
        "status_reason": record.status_reason,
        "private_key_configured": bool(record.encrypted_private_key),
        "last_checked_at": record.last_checked_at.isoformat(),
    }


def _safe_text(value: str) -> str:
    value = re.sub(r"(?i)(passphrase|password|private key)", "凭据", value or "")
    return " ".join(value.split())[:500] or "远程命令返回非零状态"


def _safe_error(error: Exception) -> str:
    return _safe_text(str(error))


def _which(executable: str) -> str | None:
    from shutil import which

    return which(executable)


def _now():
    from datetime import UTC, datetime

    return datetime.now(UTC)
