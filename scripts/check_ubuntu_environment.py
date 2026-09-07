#!/usr/bin/env python3
"""Validate a TaskHub Ubuntu host against the canonical environment baseline."""

import argparse
import hashlib
import importlib.metadata
import json
import platform
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / "config" / "ubuntu-environment-baseline.json"


def command_version(*command: str) -> str:
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def os_release() -> dict[str, str]:
    values = {}
    for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value.strip('"')
    return values


def installed_packages() -> dict[str, str]:
    return {
        distribution.metadata["Name"].lower().replace("_", "-"): distribution.version
        for distribution in importlib.metadata.distributions()
        if distribution.metadata["Name"]
    }


def locked_packages(path: Path) -> dict[str, str]:
    packages = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            name, version = line.split("==", 1)
            packages[name.lower().replace("_", "-")] = version
    return packages


def collect_snapshot() -> dict:
    release = os_release()
    ripgrep = command_version("rg", "--version").splitlines()[0]
    zip_version = re.search(r"This is Zip ([^ ]+)", command_version("zip", "-v"))
    unzip_version = re.search(r"UnZip ([^ ]+)", command_version("unzip", "-v"))
    psql_version = re.search(r"PostgreSQL\) ([^ ]+)", command_version("psql", "--version"))
    return {
        "platform": {
            "os_id": release.get("ID", ""),
            "os_version": release.get("VERSION_ID", ""),
            "architecture": platform.machine(),
        },
        "tools": {
            "python": platform.python_version(),
            "pip": importlib.metadata.version("pip"),
            "node": command_version("node", "--version").removeprefix("v"),
            "npm": command_version("npm", "--version"),
            "git": command_version("git", "--version").removeprefix("git version "),
            "ripgrep": ripgrep.removeprefix("ripgrep "),
            "jq": command_version("jq", "--version").removeprefix("jq-"),
            "zip": zip_version.group(1) if zip_version else "unknown",
            "unzip": unzip_version.group(1) if unzip_version else "unknown",
            "psql": psql_version.group(1) if psql_version else "unknown",
            "corepack": command_version("/usr/local/bin/corepack", "--version"),
            "codex_cli": command_version(
                str(Path.home() / ".local/bin/codex"), "--version"
            ).removeprefix("codex-cli "),
        },
        "venv": sys.prefix,
        "packages": installed_packages(),
    }


def compare(baseline: dict, snapshot: dict, locked: dict[str, str]) -> list[str]:
    errors = []
    for section in ("platform", "tools"):
        for key, expected in baseline[section].items():
            actual = snapshot[section].get(key)
            if actual != expected:
                errors.append(f"{section}.{key}: expected {expected}, got {actual}")
    if snapshot["venv"] != baseline["venv"]:
        errors.append(f"venv: expected {baseline['venv']}, got {snapshot['venv']}")
    missing_commands = [
        command for command in baseline.get("required_commands", []) if not shutil.which(command)
    ]
    if missing_commands:
        errors.append("missing commands: " + ", ".join(missing_commands))
    for name, expected in locked.items():
        actual = snapshot["packages"].get(name)
        if actual != expected:
            errors.append(f"package {name}: expected {expected}, got {actual or 'missing'}")
    unexpected = sorted(set(snapshot["packages"]) - set(locked) - {"pip", "setuptools"})
    if unexpected:
        errors.append("unexpected packages: " + ", ".join(unexpected))
    return errors


def template_errors(baseline: dict) -> list[str]:
    errors = []
    if socket.gethostname() != baseline["template_hostname"]:
        actual_hostname = socket.gethostname()
        errors.append(
            f"template hostname: expected {baseline['template_hostname']}, got {actual_hostname}"
        )
    sensitive = [
        Path.home() / ".config/taskhub-node/node.env",
        Path.home() / ".config/taskhub-node/providers.env",
        Path.home() / ".config/taskhub-v2/providers.env",
        Path.home() / ".codex/auth.json",
        Path.home() / ".codex-plus/auth.json",
        Path.home() / ".codex-pro/auth.json",
    ]
    present = [str(path) for path in sensitive if path.exists()]
    if present:
        errors.append("template contains production configuration: " + ", ".join(present))
    units = command_version(
        "systemctl", "list-unit-files", "--type=service", "--no-legend"
    )
    taskhub_units = [line.split()[0] for line in units.splitlines() if "taskhub" in line.lower()]
    if taskhub_units:
        errors.append("template contains TaskHub services: " + ", ".join(taskhub_units))
    sockets = command_version("ss", "-ltnH")
    ports = sorted(set(re.findall(r":(8200|8301)\s", sockets)))
    if ports:
        errors.append("template listens on production ports: " + ", ".join(ports))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--mode", choices=("node", "template"), default="node")
    args = parser.parse_args()

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    lock_path = ROOT / baseline["lock_file"]
    lock_digest = hashlib.sha256(lock_path.read_bytes()).hexdigest()
    errors = []
    if lock_digest != baseline["lock_sha256"]:
        errors.append(
            f"lock sha256: expected {baseline['lock_sha256']}, got {lock_digest}"
        )
    errors.extend(compare(baseline, collect_snapshot(), locked_packages(lock_path)))
    if args.mode == "template":
        errors.extend(template_errors(baseline))
    result = {
        "status": "qualified" if not errors else "rejected",
        "profile_id": baseline["profile_id"],
        "mode": args.mode,
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
