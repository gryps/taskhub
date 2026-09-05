from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.account_runner import run_account_workspace
from app.planner import (
    PROVIDER_DEFINITIONS,
    PlannerRequest,
    call_api_provider,
    extract_json_object,
    provider_model_for_role,
    role_config,
)
from app.provider_health import before_attempt, record_failure, record_success
from app.taskhub import connect


router = APIRouter(prefix="/taskhub/coder", tags=["coder"])
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_DIFF_BYTES = 2 * 1024 * 1024


class CodeExecutionRequest(BaseModel):
    task_id: uuid.UUID
    worker_id: str = Field(..., min_length=1, max_length=200)
    workspace_id: str = Field(..., min_length=1, max_length=200)
    baseline_commit: str = Field(..., pattern=r"^[0-9a-f]{40}$")
    requirement: str = Field(..., min_length=1, max_length=30000)
    archive_base64: str = Field(..., min_length=1)


def _task_for_execution(payload: CodeExecutionRequest) -> dict[str, Any]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("select * from taskhub_tasks where id=%s", (payload.task_id,))
            task = cur.fetchone()
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    if task["type"] != "code.change" or task["state"] != "running":
        raise HTTPException(status_code=409, detail="task is not a running code.change")
    if task.get("worker_id") != payload.worker_id or task.get("target_worker_id") != payload.worker_id:
        raise HTTPException(status_code=409, detail="worker does not own the code task")
    if task.get("workspace_id") != payload.workspace_id:
        raise HTTPException(status_code=409, detail="workspace does not match the task pipeline")
    if len(str((task.get("metadata") or {}).get("approval_content_hash") or "")) != 64:
        raise HTTPException(status_code=409, detail="human approval evidence is required")
    return task


def _safe_extract(encoded: str, destination: Path) -> None:
    try:
        archive = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail="invalid source archive") from exc
    if len(archive) > MAX_ARCHIVE_BYTES:
        raise HTTPException(status_code=413, detail="source archive is too large")
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
            members = bundle.getmembers()
            for member in members:
                path = PurePosixPath(member.name)
                if member.islnk() or member.issym() or path.is_absolute() or ".." in path.parts:
                    raise HTTPException(status_code=400, detail="source archive contains an unsafe path")
                if not (member.isfile() or member.isdir()):
                    raise HTTPException(status_code=400, detail="source archive contains an unsupported entry")
            bundle.extractall(destination, members=members, filter="data")
    except (tarfile.TarError, OSError) as exc:
        raise HTTPException(status_code=400, detail="source archive cannot be extracted") from exc


def _run(command: list[str], cwd: Path, timeout: int = 60, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, input=stdin, capture_output=True, text=True, timeout=timeout, check=False)


def _prepare_repository(root: Path, baseline: str) -> None:
    for command in (["git", "init", "-q"], ["git", "add", "-A"], ["git", "-c", "user.name=TaskHub", "-c", "user.email=taskhub@local", "commit", "-qm", "baseline"]):
        completed = _run(command, root)
        if completed.returncode != 0:
            raise HTTPException(status_code=400, detail=f"cannot prepare isolated source copy: {completed.stderr[-500:]}")
    (root / ".taskhub-baseline").write_text(baseline + "\n", encoding="ascii")


def _coder_prompt(payload: CodeExecutionRequest) -> str:
    return (
        "You are the construction role in TaskHub. Inspect this isolated repository and implement the approved requirement. "
        "Edit the repository files directly. Do not commit. Keep changes minimal and preserve existing architecture. "
        "Do not access the network, credentials, cookies, login state, production systems, or production data. "
        "Do not modify .git, lockfiles unless required, or .taskhub-baseline. Run focused local tests when practical. "
        "Finish with JSON matching the required schema.\n\n"
        f"Task ID: {payload.task_id}\nWorkspace: {payload.workspace_id}\n"
        f"Approved requirement:\n{payload.requirement}"
    )


def _api_patch_prompt(payload: CodeExecutionRequest, root: Path) -> list[dict[str, str]]:
    files: list[str] = []
    used = 0
    for path in sorted(item for item in root.rglob("*") if item.is_file() and ".git" not in item.parts):
        relative = path.relative_to(root).as_posix()
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        block = f"\n--- {relative} ---\n{content[:16000]}"
        if used + len(block) > 180000:
            files.append(f"\n--- remaining file: {relative} ---")
            continue
        files.append(block)
        used += len(block)
    schema = '{"summary":string,"unified_diff":string,"tests":[string]}'
    return [
        {"role": "system", "content": "You implement approved code changes. Return only JSON with schema " + schema + ". unified_diff must be a git-compatible patch and must not touch credentials or production data."},
        {"role": "user", "content": f"Requirement:\n{payload.requirement}\nRepository files:\n{''.join(files)}"},
    ]


def _validated_diff(root: Path) -> str:
    completed = _run(["git", "diff", "--binary", "--", ".", ":(exclude).taskhub-baseline"], root)
    diff = completed.stdout
    if completed.returncode != 0 or not diff.strip():
        raise ValueError("model produced no repository changes")
    if len(diff.encode()) > MAX_DIFF_BYTES:
        raise ValueError("model diff exceeds the safety limit")
    check = _run(["git", "apply", "--check", "--reverse", "-"], root, stdin=diff)
    if check.returncode != 0:
        raise ValueError("generated diff failed reverse validation")
    return diff


def _changed_paths(root: Path) -> list[str]:
    completed = _run(["git", "diff", "--name-only", "--", "."], root)
    if completed.returncode != 0:
        raise ValueError("cannot list generated paths")
    return [line for line in completed.stdout.splitlines() if line]


def _execute_with_fallback(payload: CodeExecutionRequest, root: Path, project: str) -> dict[str, Any]:
    role = role_config("coder")
    attempts: list[dict[str, Any]] = []
    for provider in role["provider_order"]:
        allowed, gate = before_attempt(provider)
        if not allowed:
            attempts.append({"provider": provider, "status": "skipped", "reason": "cooldown", "provider_health": gate})
            continue
        _run(["git", "reset", "--hard", "-q", "HEAD"], root)
        _run(["git", "clean", "-fdq"], root)
        definition = PROVIDER_DEFINITIONS.get(provider, {"kind": "api"})
        if definition["kind"] == "account":
            model = provider_model_for_role(provider, role)
            content, attempt = run_account_workspace(
                provider,
                _coder_prompt(payload),
                str(root),
                None if model == "account_default" else model,
            )
            attempt.update({"role": "coder", "provider": provider})
            attempts.append(attempt)
            if content is None:
                attempt["provider_health"] = record_failure(provider, str(attempt.get("reason") or "provider_error"))
                continue
            try:
                report = extract_json_object(content)
                diff = _validated_diff(root)
            except (ValueError, json.JSONDecodeError):
                attempt.update({"status": "failed", "reason": "invalid_code_change"})
                attempt["provider_health"] = record_failure(provider, "invalid_code_change")
                continue
        else:
            request = PlannerRequest(requirement=payload.requirement, project=project)
            report, attempt = call_api_provider(provider, role, request, [], _api_patch_prompt(payload, root))
            attempts.append(attempt)
            if report is None:
                attempt["provider_health"] = record_failure(provider, str(attempt.get("reason") or "provider_error"))
                continue
            diff = str(report.get("unified_diff") or "")
            check = _run(["git", "apply", "--check", "-"], root, stdin=diff)
            if not diff.strip() or check.returncode != 0:
                attempt.update({"status": "failed", "reason": "invalid_code_change"})
                attempt["provider_health"] = record_failure(provider, "invalid_code_change")
                continue
            applied = _run(["git", "apply", "-"], root, stdin=diff)
            if applied.returncode != 0:
                attempt.update({"status": "failed", "reason": "invalid_code_change"})
                attempt["provider_health"] = record_failure(provider, "invalid_code_change")
                continue
            diff = _validated_diff(root)
        attempt["provider_health"] = record_success(provider)
        try:
            from app.advanced import record_model_usage

            record_model_usage(
                project,
                str(payload.task_id),
                "coder",
                provider,
                str(attempt.get("model") or role["model"]),
                "succeeded",
                attempt.get("usage"),
            )
        except Exception:
            pass
        return {
            "provider": provider,
            "model": attempt.get("model") or role["model"],
            "summary": str(report.get("summary") or "施工模型已生成代码变更。"),
            "tests": report.get("tests") if isinstance(report.get("tests"), list) else [],
            "unified_diff": diff,
            "diff_sha256": hashlib.sha256(diff.encode()).hexdigest(),
            "touched_paths": _changed_paths(root),
            "attempts": attempts,
        }
    reasons = "; ".join(f"{item.get('provider')}:{item.get('reason', 'failed')}" for item in attempts)
    raise HTTPException(status_code=503, detail=f"all coder providers failed: {reasons or 'no provider'}")


@router.post("/execute")
def execute_code(payload: CodeExecutionRequest) -> dict[str, Any]:
    task = _task_for_execution(payload)
    with tempfile.TemporaryDirectory(prefix="taskhub-code-") as temp_dir:
        root = Path(temp_dir) / "repo"
        root.mkdir()
        _safe_extract(payload.archive_base64, root)
        _prepare_repository(root, payload.baseline_commit)
        return _execute_with_fallback(payload, root, str(task["project"]))
