from __future__ import annotations

import os
import re
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from taskhub_v2.domain.hosts import HostConnection
from taskhub_v2.persistence.hosts import PhysicalHostRecord


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
memory_available=$(awk '/MemAvailable:/ {print $2 * 1024}' /proc/meminfo)
disk_total=$(df -Pk / | awk 'NR == 2 {print $2 * 1024}')
disk_value=$(df -Pk / | awk 'NR == 2 {print $4 * 1024}')
load_value=$(awk '{print $1}' /proc/loadavg)
docker_value=$(docker_run version --format '{{.Server.Version}}')
callback_value=failed
if command -v curl >/dev/null 2>&1; then
  curl -fsS --max-time 6 "${callback%/}/api/health" >/dev/null && callback_value=passed
elif command -v wget >/dev/null 2>&1; then
  wget -q -T 6 -O /dev/null "${callback%/}/api/health" && callback_value=passed
fi
printf 'OS=%s\nARCH=%s\nCPU=%s\nMEMORY=%s\n' \
  "$os_value" "$arch_value" "$cpu_value" "$memory_value"
printf 'MEMORY_AVAILABLE=%s\nDISK_TOTAL=%s\nDISK=%s\nLOAD=%s\nDOCKER=%s\nCALLBACK=%s\n' \
  "$memory_available" "$disk_total" "$disk_value" "$load_value" \
  "$docker_value" "$callback_value"
"""


def probe_host(request: HostConnection, callback: str) -> dict[str, Any]:
    require_ssh_tools()
    host_key, fingerprint = scan_host_key(request.address, request.port)
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
    if not callback.startswith(("http://", "https://")):
        raise HostAdmissionError("请先在平台设置中配置远程主机可访问的 Node Agent 回连地址")
    private_key = request.private_key.get_secret_value().strip()
    if "PRIVATE KEY-----" not in private_key or len(private_key) > 32768:
        raise HostAdmissionError("SSH 私钥格式无效或文件过大")
    facts = run_remote_probe(request, private_key, host_key, callback)
    if facts["callback"] != "passed":
        raise HostAdmissionError(f"远程主机无法访问 Node Agent 回连地址 {callback}/api/health")
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


def require_ssh_tools() -> None:
    for executable in ("ssh", "ssh-keyscan", "ssh-keygen"):
        if not which(executable):
            raise HostAdmissionError(f"Seed 镜像缺少 {executable}，请升级控制节点镜像")


def scan_host_key(address: str, port: int) -> tuple[str, str]:
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
        raise HostAdmissionError(f"无法读取 SSH 主机指纹：{safe_error(exc)}") from exc
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


def run_remote_probe(
    request: HostConnection, private_key: str, host_key: str, callback: str
) -> dict[str, Any]:
    result = _run_ssh(
        request.address,
        request.port,
        request.username,
        private_key,
        host_key,
        PROBE_SCRIPT,
        25,
        [shlex.quote(request.docker_access), shlex.quote(callback)],
    )
    if result.returncode:
        raise HostAdmissionError(f"SSH 准入检测失败：{safe_text(result.stderr)}")
    values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    required = {
        "OS", "ARCH", "CPU", "MEMORY", "MEMORY_AVAILABLE", "DISK_TOTAL",
        "DISK", "LOAD", "DOCKER", "CALLBACK",
    }
    if not required.issubset(values):
        raise HostAdmissionError("远程主机返回的准入信息不完整")
    return {
        "os": values["OS"],
        "architecture": values["ARCH"],
        "cpu_count": int(float(values["CPU"])),
        "memory_bytes": int(float(values["MEMORY"])),
        "memory_available_bytes": int(float(values["MEMORY_AVAILABLE"])),
        "disk_total_bytes": int(float(values["DISK_TOTAL"])),
        "disk_available_bytes": int(float(values["DISK"])),
        "load_average_1m": float(values["LOAD"]),
        "docker_version": values["DOCKER"],
        "callback": values["CALLBACK"],
    }


def run_remote_script(
    record: PhysicalHostRecord, private_key: str, script: str, timeout: int
) -> str:
    payload = record.payload
    result = _run_ssh(
        payload["address"], payload["port"], payload["username"], private_key,
        record.host_key, script, timeout,
    )
    if result.returncode:
        raise HostAdmissionError(f"SSH 远程操作失败：{safe_text(result.stderr)}")
    return result.stdout


def _run_ssh(
    address: str, port: int, username: str, private_key: str, host_key: str,
    script: str, timeout: int, arguments: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory(prefix="taskhub-host-") as directory:
        key_path = Path(directory, "identity")
        known_hosts_path = Path(directory, "known_hosts")
        key_path.write_text(private_key.strip() + "\n", encoding="utf-8")
        key_path.chmod(0o600)
        known_hosts_path.write_text(host_key + "\n", encoding="utf-8")
        command = [
            "ssh", "-i", str(key_path), "-p", str(port), "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=8", "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes", "-o",
            f"UserKnownHostsFile={known_hosts_path}", f"{username}@{address}", "sh", "-s",
        ]
        if arguments:
            command.extend(["--", *arguments])
        try:
            return subprocess.run(
                command, input=script, capture_output=True, text=True, timeout=timeout,
                env={**os.environ, "LC_ALL": "C"}, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HostAdmissionError(f"SSH 远程操作失败：{safe_error(exc)}") from exc


def safe_text(value: str) -> str:
    value = re.sub(r"(?i)(passphrase|password|private key)", "凭据", value or "")
    return " ".join(value.split())[:500] or "远程命令返回非零状态"


def safe_error(error: Exception) -> str:
    return safe_text(str(error))


def which(executable: str) -> str | None:
    from shutil import which as find_executable

    return find_executable(executable)
