#!/usr/bin/env python3
"""Pair a local development agent with a TaskHub Seed without exposing credentials."""

import argparse
import getpass
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin

import httpx


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="Seed URL, for example https://host:8200")
    parser.add_argument(
        "--ca-file", help="CA or self-signed Seed certificate used for TLS verification"
    )
    host = os.uname().nodename if hasattr(os, "uname") else getpass.getuser()
    parser.add_argument("--label", default=f"Codex on {host}")
    parser.add_argument(
        "--token-file", required=True, help="Local 0600 file for the bearer credential"
    )
    parser.add_argument(
        "--connection-file", help="Optional connection JSON to update with the token path"
    )
    parser.add_argument("--timeout-seconds", type=int, default=600)
    return parser.parse_args()


def atomic_private_write(path: Path, value: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            target.write(value)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        os.chmod(temporary, 0o600)
        temporary.replace(path)
        _restrict_windows_acl(path)
    finally:
        temporary.unlink(missing_ok=True)


def _restrict_windows_acl(path: Path) -> None:
    if os.name != "nt":
        return
    subprocess.run(
        [
            "icacls",
            str(path),
            "/inheritance:r",
            "/grant:r",
            f"{getpass.getuser()}:(R,W)",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def update_connection(path: Path, token_file: Path) -> None:
    value = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    if not isinstance(value, dict):
        raise ValueError("connection file must contain a JSON object")
    value.update(
        {
            "api_auth": "bearer-token",
            "agent_token_file": str(token_file.resolve()),
            "auth_status": "agent-authenticated",
        }
    )
    atomic_private_write(path, json.dumps(value, ensure_ascii=False, indent=2))


def pair(args: argparse.Namespace) -> None:
    base_url = args.base_url.rstrip("/") + "/"
    verify: bool | str = args.ca_file or True
    deadline = time.monotonic() + max(30, args.timeout_seconds)
    with httpx.Client(verify=verify, timeout=15, trust_env=False) as client:
        response = client.post(
            urljoin(base_url, "api/auth/agent-pairings/start"),
            json={"label": args.label},
        )
        response.raise_for_status()
        pairing = response.json()
        print(f"打开 {base_url}")
        print("进入 系统配置 → 高级设置 → 用户、权限与访问安全。")
        print(f"确认配对码 {pairing['user_code']}，然后点击“批准”。")
        while time.monotonic() < deadline:
            time.sleep(max(1, int(pairing.get("poll_interval_seconds", 2))))
            exchanged = client.post(
                urljoin(
                    base_url,
                    f"api/auth/agent-pairings/{pairing['pairing_id']}/exchange",
                ),
                json={"device_secret": pairing["device_secret"]},
            )
            exchanged.raise_for_status()
            result = exchanged.json()
            if result["status"] == "pending":
                continue
            if result["status"] == "rejected":
                raise RuntimeError("配对已被拒绝")
            token_file = Path(args.token_file).expanduser()
            atomic_private_write(token_file, result["token"])
            if args.connection_file:
                update_connection(Path(args.connection_file).expanduser(), token_file)
            print(f"连接已认证；凭据已安全保存到 {token_file}。")
            return
    raise TimeoutError("等待管理员批准超时；未写入任何凭据")


def main() -> int:
    try:
        pair(arguments())
    except (OSError, ValueError, RuntimeError, TimeoutError, httpx.HTTPError) as error:
        print(f"连接失败：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
