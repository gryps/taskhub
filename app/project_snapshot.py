from __future__ import annotations

import hashlib
import os
import subprocess
from typing import Any


def capture_project_snapshot() -> dict[str, Any]:
    host = os.getenv("PROJECT_SOURCE_HOST", "192.168.31.31")
    root = os.getenv("PROJECT_SOURCE_ROOT", "/home/gryps/apps/douyin-listing-workbench")
    workspace_id = os.getenv("PROJECT_WORKSPACE_ID", "douyin-listing-workbench@31")
    command = [
        "git",
        "-C",
        root,
        "status",
        "--porcelain=v1",
        "--branch",
        "--untracked-files=normal",
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
    commit = subprocess.run(
        ["git", "-C", root, "rev-parse", "HEAD"],
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
