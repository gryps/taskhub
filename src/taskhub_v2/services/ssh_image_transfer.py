from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any


class SSHImageTransferError(RuntimeError):
    pass


def load_docker_image(record: Any, private_key: str, image_path: Path, timeout: int) -> str:
    payload = record.payload
    with tempfile.TemporaryDirectory(prefix="taskhub-host-") as directory:
        key_path = Path(directory, "identity")
        known_hosts_path = Path(directory, "known_hosts")
        key_path.write_text(private_key.strip() + "\n", encoding="utf-8")
        key_path.chmod(0o600)
        known_hosts_path.write_text(record.host_key + "\n", encoding="utf-8")
        docker = "sudo -n docker load" if payload["docker_access"] == "sudo" else "docker load"
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
            docker,
        ]
        try:
            with image_path.open("rb") as source:
                result = subprocess.run(
                    command,
                    stdin=source,
                    capture_output=True,
                    text=False,
                    timeout=timeout,
                    env={**os.environ, "LC_ALL": "C"},
                    check=False,
                )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SSHImageTransferError(f"SSH 镜像传输失败：{_safe_error(exc)}") from exc
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")
    if result.returncode:
        raise SSHImageTransferError(f"远程 docker load 失败：{_safe_text(stderr)}")
    return stdout


def _safe_text(value: str) -> str:
    value = re.sub(r"(?i)(passphrase|password|private key)", "凭据", value or "")
    return " ".join(value.split())[:500] or "远程命令返回非零状态"


def _safe_error(error: Exception) -> str:
    return _safe_text(str(error))
