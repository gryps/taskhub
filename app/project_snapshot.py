from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
from typing import Any


def capture_project_snapshot() -> dict[str, Any]:
    host = os.getenv("PROJECT_SOURCE_HOST", "192.168.31.17")
    root = os.getenv("PROJECT_SOURCE_ROOT", "/home/gryps/.openclaw/workspace/douyin-listing-workbench")
    workspace_id = os.getenv("PROJECT_WORKSPACE_ID", "douyin-listing-workbench@authority")
    local_hosts = {"", "localhost", "127.0.0.1", os.uname().nodename.lower()}
    git_status = ["git", "-C", root, "status", "--porcelain=v1", "--branch", "--untracked-files=normal"]
    if host.lower() in local_hosts:
        command = git_status
    else:
        remote = " ".join(shlex.quote(item) for item in git_status)
        command = [
            "ssh", "-p", os.getenv("PROJECT_SOURCE_SSH_PORT", "22"), "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=8", f"{os.getenv('PROJECT_SOURCE_SSH_USER', 'gryps')}@{host}", remote,
        ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=10)
    if completed.returncode != 0:
        return {
            "source_host": host,
            "repo_root": root,
            "workspace_id": workspace_id,
            "snapshot_error": completed.stderr[-500:] or "git status failed",
        }
    status = completed.stdout
    commit_command = ["git", "-C", root, "rev-parse", "HEAD"]
    if host.lower() not in local_hosts:
        commit_command = [
            "ssh", "-p", os.getenv("PROJECT_SOURCE_SSH_PORT", "22"), "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=8", f"{os.getenv('PROJECT_SOURCE_SSH_USER', 'gryps')}@{host}",
            " ".join(shlex.quote(item) for item in commit_command),
        ]
    commit = subprocess.run(
        commit_command,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()
    branch_line = status.splitlines()[0] if status.startswith("## ") else ""
    branch = branch_line[3:].split("...", 1)[0].strip() if branch_line else ""
    changes = status.splitlines()[1:] if branch_line else status.splitlines()
    return {
        "source_host": host,
        "repo_root": root,
        "workspace_id": workspace_id,
        "branch": branch,
        "commit": commit,
        "dirty": bool(changes),
        "status_hash": hashlib.sha256(status.encode()).hexdigest(),
        "change_count": len(changes),
    }
