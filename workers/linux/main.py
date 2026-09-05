from __future__ import annotations

import os
import base64
import hmac
import hashlib
import io
import json
import platform
import shlex
import socket
import subprocess
import tarfile
import time
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path, PurePath
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

load_dotenv()

STARTED_AT = time.time()
POLL_TASK: asyncio.Task[None] | None = None


class TaskExecutionError(Exception):
    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(str(payload.get("message", payload.get("code", "task failed"))))
        self.payload = payload


class ToolRequest(BaseModel):
    tool: str
    input: str | None = None


def worker_name() -> str:
    return os.getenv("WORKER_NAME", socket.gethostname())


def supported_task_types() -> list[str]:
    raw = os.getenv(
        "WORKER_TASK_TYPES",
        "echo,system_info,ops.note,project.context.sync,project.git.status,requirement.split,code.change,code.diff.preview,code.change.apply,test.run,quality.env.check,workspace.bootstrap",
    )
    return [item.strip() for item in raw.split(",") if item.strip()]


def taskhub_poll_enabled() -> bool:
    return os.getenv("TASKHUB_POLL", "false").lower() in {"1", "true", "yes", "on"}


def taskhub_headers() -> dict[str, str]:
    return {
        "x-taskhub-worker-token": os.getenv("TASKHUB_WORKER_TOKEN", ""),
        "x-worker-id": worker_name(),
    }


def execute_tool(tool: str, input_value: str | None = None) -> dict[str, str]:
    if tool == "echo":
        return {"tool": tool, "result": input_value or ""}
    if tool == "ops.note":
        return {"tool": tool, "result": input_value or "noted"}
    if tool == "system_info":
        return {
            "tool": tool,
            "result": f"{socket.gethostname()} | {platform.platform()} | {platform.processor()}",
        }
    raise HTTPException(status_code=400, detail=f"unsupported tool: {tool}")


def task_input_as_text(task: dict[str, Any]) -> str:
    payload = task.get("input") or {}
    if isinstance(payload, dict):
        for key in ("text", "message", "note", "requirement"):
            value = payload.get(key)
            if value is not None:
                return str(value)
        return str(payload)
    return str(payload)


async def _execute_taskhub_task(task: dict[str, Any]) -> dict[str, Any]:
    task_type = str(task.get("type", ""))
    if task_type in {"echo", "ops.note", "system_info"}:
        return execute_tool(task_type, task_input_as_text(task))
    if task_type == "project.context.sync":
        return await asyncio.to_thread(sync_project_context, task)
    if task_type == "project.git.status":
        return await asyncio.to_thread(project_git_status)
    if task_type == "requirement.split":
        return split_requirement(task)
    if task_type == "code.change":
        payload = task_payload(task)
        if str(payload.get("mode") or "execute") == "plan_only":
            return await asyncio.to_thread(plan_code_change, task)
        return await asyncio.to_thread(execute_code_change, task)
    if task_type == "code.diff.preview":
        return await asyncio.to_thread(preview_code_diff, task)
    if task_type == "code.change.apply":
        return await asyncio.to_thread(apply_code_change, task)
    if task_type == "test.run":
        return await asyncio.to_thread(run_project_test, task)
    if task_type == "quality.env.check":
        return await asyncio.to_thread(check_quality_environment, task)
    if task_type == "workspace.bootstrap":
        return await asyncio.to_thread(bootstrap_workspace, task)
    raise ValueError(f"unsupported task type: {task_type}")


def archive_task_result(task: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    root = Path(os.getenv("ARTIFACT_ROOT", "/home/gryps/artifacts/langgraph"))
    task_dir = root / str(task["id"])
    task_dir.mkdir(parents=True, exist_ok=True)
    result_path = task_dir / "result.json"
    encoded = json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2).encode()
    result_path.write_bytes(encoded)
    manifest = {
        "host": worker_name(),
        "task_id": str(task["id"]),
        "files": [
            {
                "name": result_path.name,
                "path": str(result_path),
                "size": len(encoded),
                "sha256": hashlib.sha256(encoded).hexdigest(),
            }
        ],
    }
    return {**result, "artifacts": manifest}


async def execute_taskhub_task(task: dict[str, Any]) -> dict[str, Any]:
    return archive_task_result(task, await _execute_taskhub_task(task))


def sync_project_context(task: dict[str, Any]) -> dict[str, Any]:
    payload = task.get("input") or {}
    files = payload.get("files") if isinstance(payload, dict) else None
    requested = files if isinstance(files, list) else []
    allowed = {
        "AGENTS.md",
        "PROJECT_STATE.md",
        "CONTEXT_PACK.md",
        "README.md",
        "package.json",
        "docs/langgraph/migration-three-stage-plan.md",
        "docs/langgraph/prd-stage1-taskhub.md",
        "docs/langgraph/prd-stage1-web-console.md",
    }
    selected = [str(item) for item in requested if str(item) in allowed] or [
        "AGENTS.md",
        "PROJECT_STATE.md",
        "CONTEXT_PACK.md",
        "README.md",
    ]
    host = os.getenv("PROJECT_CONTEXT_HOST", "192.168.31.31")
    root = os.getenv("PROJECT_CONTEXT_ROOT", "/home/gryps/apps/douyin-listing-workbench")
    summaries = []
    for relative_path in selected:
        remote_path = f"{root}/{relative_path}"
        command = f"test -f {shlex.quote(remote_path)} && sed -n '1,120p' {shlex.quote(remote_path)}"
        completed = run_project_command(command, timeout=20, absolute_command=True)
        summaries.append(
            {
                "file": relative_path,
                "available": completed.returncode == 0,
                "excerpt": completed.stdout[:4000] if completed.returncode == 0 else "",
                "error": completed.stderr[:500] if completed.returncode != 0 else "",
            }
        )
    return {
        "tool": "project.context.sync",
        "project_host": host,
        "project_root": root,
        "files": summaries,
    }


def split_requirement(task: dict[str, Any]) -> dict[str, Any]:
    payload = task.get("input") or {}
    requirement = ""
    if isinstance(payload, dict):
        requirement = str(payload.get("requirement") or payload.get("note") or payload)
    else:
        requirement = str(payload)
    return {
        "tool": "requirement.split",
        "requirement": requirement,
        "proposed_tasks": [
            {
                "type": "project.context.sync",
                "title": "同步当前项目状态",
                "done": "读取 AGENTS.md、PROJECT_STATE.md、CONTEXT_PACK.md 和 README.md 摘要。",
            },
            {
                "type": "code.change",
                "title": "按需求实现受控代码改动",
                "done": "提交明确 diff，不触碰密钥、登录态和生产数据。",
            },
            {
                "type": "test.run",
                "title": "运行项目质量入口",
                "done": "记录命令、退出码和失败摘要。",
            },
            {
                "type": "review.human",
                "title": "人工确认是否进入下一步",
                "done": "用户确认范围、风险和是否允许继续执行。",
            },
        ],
        "non_goals": [
            "不自动发布抖店商品。",
            "不直接修改生产数据库。",
            "不记录或展示密钥、Cookie、Token、登录态。",
        ],
    }


def project_git_status() -> dict[str, Any]:
    host = os.getenv("PROJECT_CONTEXT_HOST", "192.168.31.31")
    root = os.getenv("PROJECT_CONTEXT_ROOT", "/home/gryps/apps/douyin-listing-workbench")
    command = (
        f"cd {shlex.quote(root)} && "
        "git rev-parse --short HEAD && "
        "printf '\\n---STATUS---\\n' && "
        "git status --short"
    )
    completed = run_project_command(command, timeout=30, absolute_command=True)
    if completed.returncode != 0:
        raise TaskExecutionError(
            {
                "code": "GIT_STATUS_FAILED",
                "message": completed.stderr[-1000:] or "git status failed",
                "exit_code": completed.returncode,
            }
        )
    head, _, status = completed.stdout.partition("\n---STATUS---\n")
    return {
        "tool": "project.git.status",
        "project_host": host,
        "project_root": root,
        "head": head.strip(),
        "status_short": status.strip().splitlines() if status.strip() else [],
    }


def allowed_test_commands() -> dict[str, str]:
    return {
        "check": "npm run check",
        "test": "npm run test",
        "web.check": "npm --prefix apps/web run check",
        "web.test": "npm --prefix apps/web run test",
        "frontend.check": "npm run check --workspace apps/web",
        "api.test": "scripts/api_run -m pytest -q apps/api/tests",
        "api.lint": "scripts/api_run_app -m ruff check app scripts tests",
        "api.typecheck": "scripts/api_run_app -m mypy app scripts",
        "executor.check": "scripts/executor_run -m ruff check apps/executor/src apps/executor/tests && scripts/executor_run -m mypy --config-file apps/executor/pyproject.toml apps/executor/src apps/executor/tests && scripts/executor_run -m pytest -q apps/executor/tests",
        "executor.test": "scripts/executor_run -m pytest -q apps/executor/tests",
        "executor.lint": "scripts/executor_run -m ruff check apps/executor/src apps/executor/tests",
        "executor.typecheck": "scripts/executor_run -m mypy --config-file apps/executor/pyproject.toml apps/executor/src apps/executor/tests",
    }


def code_change_text(task: dict[str, Any]) -> str:
    payload = task.get("input") or {}
    if isinstance(payload, dict):
        for key in ("requirement", "request", "change", "note", "text"):
            value = payload.get(key)
            if value:
                return str(value)
    return task_input_as_text(task)


def classify_code_change_files(text: str) -> list[str]:
    lowered = text.lower()
    candidates: list[str] = []
    if any(marker in lowered for marker in ("api", "接口", "后端", "商品", "店铺", "product", "shop")):
        candidates.extend(
            [
                "apps/api/app/api/v1/",
                "apps/api/app/services/",
                "apps/api/tests/",
            ]
        )
    if any(marker in lowered for marker in ("h5", "前端", "页面", "web", "ui", "按钮", "列表")):
        candidates.extend(
            [
                "apps/web/src/",
                "apps/web/tests/",
            ]
        )
    if any(marker in lowered for marker in ("文档", "prd", "方案", "说明", "docs")):
        candidates.append("docs/")
    return candidates or ["需要人工确认影响文件范围"]


def plan_code_change(task: dict[str, Any]) -> dict[str, Any]:
    text = code_change_text(task).strip()
    payload = task.get("input") or {}
    mode = str(payload.get("mode") or "plan_only") if isinstance(payload, dict) else "plan_only"
    requested_files = payload.get("files") if isinstance(payload, dict) else None
    file_hints = [str(item) for item in requested_files] if isinstance(requested_files, list) else []
    host = os.getenv("PROJECT_CONTEXT_HOST", "192.168.31.31")
    root = os.getenv("PROJECT_CONTEXT_ROOT", "/home/gryps/apps/douyin-listing-workbench")
    status = project_git_status()
    return {
        "tool": "code.change",
        "mode": mode,
        "will_modify_files": False,
        "project_host": host,
        "project_root": root,
        "request": text,
        "file_hints": file_hints,
        "candidate_paths": file_hints or classify_code_change_files(text),
        "workcopy_head": status.get("head"),
        "workcopy_dirty": bool(status.get("status_short")),
        "workcopy_status_sample": (status.get("status_short") or [])[:20],
        "proposed_steps": [
            "确认需求边界和不可触碰范围。",
            "读取候选文件与现有测试。",
            "生成最小 diff。",
            "运行对应质量命令。",
            "把 diff、测试结果和风险交给 review.human 审批。",
        ],
        "safety_rules": [
            "初级阶段 code.change 不直接写文件。",
            "不得修改密钥、Cookie、Token、登录态、生产配置和生产数据。",
            "工作副本存在既有 dirty/untracked 状态时，必须在结果里显式提示。",
            "真正改代码前需要人工批准具体文件范围。",
        ],
        "next": "查看计划后创建 code.diff.preview 或进入 code.change.apply dry_run 校验。",
    }


def configured_workspace_id() -> str:
    return os.getenv("PROJECT_WORKSPACE_ID", "").strip()


def require_task_workspace(task: dict[str, Any]) -> str:
    expected = configured_workspace_id()
    actual = str(task.get("workspace_id") or "").strip()
    if not expected:
        raise TaskExecutionError({"code": "WORKSPACE_ID_NOT_CONFIGURED", "message": "PROJECT_WORKSPACE_ID is required"})
    if actual != expected:
        raise TaskExecutionError(
            {
                "code": "WORKSPACE_MISMATCH",
                "message": "task workspace does not match this worker",
                "task_workspace_id": actual,
                "worker_workspace_id": expected,
            }
        )
    return expected


def git_output(command: str, timeout: int = 30) -> str:
    completed = run_project_command(command, timeout=timeout)
    if completed.returncode != 0:
        raise TaskExecutionError(
            {"code": "GIT_EVIDENCE_FAILED", "message": completed.stderr[-1000:] or command, "command": command}
        )
    return completed.stdout


def workcopy_evidence() -> dict[str, Any]:
    head = git_output("git rev-parse HEAD").strip()
    status = git_output("git status --porcelain=v1 --untracked-files=all")
    diff = git_output("git diff --binary HEAD --", timeout=60)
    return {
        "workcopy_head": head,
        "workcopy_status_sha256": hashlib.sha256((status + "\0" + diff).encode()).hexdigest(),
        "workcopy_dirty": bool(status.strip()),
        "workcopy_status_sample": status.splitlines()[:40],
    }


def source_archive() -> str:
    root = Path(os.getenv("PROJECT_CONTEXT_ROOT", ""))
    listed = git_output("git ls-files -z --cached --others --exclude-standard")
    names = [item for item in listed.split("\0") if item]
    denied_names = {".env", ".env.local", "credentials.json", "auth.json"}
    buffer = io.BytesIO()
    total = 0
    with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
        for name in names:
            path = root / name
            if not path.is_file() or path.is_symlink() or path.name in denied_names or path.suffix.lower() in {".pem", ".key", ".p12"}:
                continue
            size = path.stat().st_size
            total += size
            if total > 16 * 1024 * 1024:
                raise TaskExecutionError({"code": "SOURCE_TOO_LARGE", "message": "source snapshot exceeds 16 MiB"})
            info = bundle.gettarinfo(str(path), arcname=name)
            with path.open("rb") as handle:
                bundle.addfile(info, handle)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def forbidden_generated_paths(paths: list[str]) -> list[str]:
    denied: list[str] = []
    for value in paths:
        path = PurePath(value)
        lowered = value.lower()
        if (
            path.name in {".env", ".env.local", "credentials.json", "auth.json"}
            or path.suffix.lower() in {".pem", ".key", ".p12"}
            or any(part in {".git", "runtime", "production-data"} for part in path.parts)
            or lowered.startswith("data/")
        ):
            denied.append(value)
    return denied


def execute_code_change(task: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_task_workspace(task)
    payload = task_payload(task)
    requirement = str(payload.get("requirement") or "").strip()
    if not requirement:
        raise TaskExecutionError({"code": "MISSING_REQUIREMENT", "message": "code.change requirement is required"})
    approval_hash = str((task.get("metadata") or {}).get("approval_content_hash") or "")
    if len(approval_hash) != 64:
        raise TaskExecutionError({"code": "APPROVAL_EVIDENCE_REQUIRED", "message": "approved content hash is required"})

    before = workcopy_evidence()
    controller_url = os.getenv("CONTROLLER_URL", "").rstrip("/")
    with httpx.Client(timeout=float(os.getenv("CODE_EXECUTION_TIMEOUT", "1300"))) as client:
        response = client.post(
            f"{controller_url}/taskhub/coder/execute",
            json={
                "task_id": str(task["id"]),
                "worker_id": worker_name(),
                "workspace_id": workspace_id,
                "baseline_commit": before["workcopy_head"],
                "requirement": requirement,
                "archive_base64": source_archive(),
            },
            headers=taskhub_headers(),
        )
    if response.status_code >= 400:
        raise TaskExecutionError(
            {"code": "CODER_MODEL_FAILED", "message": response.text[-2000:], "status_code": response.status_code}
        )
    model_result = response.json()
    unified_diff = str(model_result.get("unified_diff") or "")
    _, touched_paths = diff_touches_allowed_paths(unified_diff, [])
    if not touched_paths:
        raise TaskExecutionError({"code": "EMPTY_MODEL_DIFF", "message": "model returned no changed paths"})
    denied = forbidden_generated_paths(touched_paths)
    if denied:
        raise TaskExecutionError({"code": "FORBIDDEN_CODE_PATH", "message": "model diff touches protected paths", "paths": denied})
    check = run_project_command(f"printf %s {shell_single_quote(unified_diff)} | git apply --check -", timeout=60)
    if check.returncode != 0:
        raise TaskExecutionError({"code": "MODEL_DIFF_CHECK_FAILED", "message": check.stderr[-2000:]})
    applied = run_project_command(f"printf %s {shell_single_quote(unified_diff)} | git apply -", timeout=60)
    if applied.returncode != 0:
        raise TaskExecutionError({"code": "MODEL_DIFF_APPLY_FAILED", "message": applied.stderr[-2000:]})
    after = workcopy_evidence()
    return {
        "tool": "code.change",
        "mode": "execute",
        "will_modify_files": True,
        "workspace_id": workspace_id,
        "baseline_commit": before["workcopy_head"],
        "before_status_sha256": before["workcopy_status_sha256"],
        "after_status_sha256": after["workcopy_status_sha256"],
        "workcopy_head": after["workcopy_head"],
        "diff_sha256": str(model_result.get("diff_sha256") or hashlib.sha256(unified_diff.encode()).hexdigest()),
        "touched_paths": touched_paths,
        "provider": model_result.get("provider"),
        "model": model_result.get("model"),
        "summary": model_result.get("summary"),
        "model_tests": model_result.get("tests") or [],
        "attempts": model_result.get("attempts") or [],
    }


def task_payload(task: dict[str, Any]) -> dict[str, Any]:
    payload = task.get("input") or {}
    return payload if isinstance(payload, dict) else {}


def normalize_relative_paths(paths: Any) -> list[str]:
    if not isinstance(paths, list):
        return []
    result: list[str] = []
    for item in paths:
        value = str(item).strip()
        if not value or value.startswith("/") or ".." in value.split("/"):
            continue
        result.append(value)
    return result


def preview_code_diff(task: dict[str, Any]) -> dict[str, Any]:
    payload = task_payload(task)
    paths = normalize_relative_paths(payload.get("paths") or payload.get("files"))
    path_args = " ".join(shlex.quote(path) for path in paths)
    command = f"git diff -- {path_args}" if path_args else "git diff --"
    completed = run_project_command(command, timeout=30)
    if completed.returncode != 0:
        raise TaskExecutionError(
            {
                "code": "DIFF_PREVIEW_FAILED",
                "message": completed.stderr[-1000:] or "git diff failed",
                "exit_code": completed.returncode,
            }
        )
    diff = completed.stdout[-20000:]
    return {
        "tool": "code.diff.preview",
        "project_host": os.getenv("PROJECT_CONTEXT_HOST", "192.168.31.31"),
        "project_root": os.getenv("PROJECT_CONTEXT_ROOT", "/home/gryps/apps/douyin-listing-workbench"),
        "paths": paths,
        "command": command,
        "has_diff": bool(diff.strip()),
        "diff_tail": diff,
        "truncated": len(completed.stdout) > len(diff),
        "will_modify_files": False,
    }


def diff_touches_allowed_paths(unified_diff: str, allowed_paths: list[str]) -> tuple[bool, list[str]]:
    touched: list[str] = []
    for line in unified_diff.splitlines():
        if not line.startswith(("+++ ", "--- ")):
            continue
        path = line[4:].strip()
        if path == "/dev/null":
            continue
        if path.startswith(("a/", "b/")):
            path = path[2:]
        if path and path not in touched:
            touched.append(path)
    if not allowed_paths:
        return False, touched
    return all(any(path == allowed or path.startswith(f"{allowed.rstrip('/')}/") for allowed in allowed_paths) for path in touched), touched


def shell_single_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def apply_code_change(task: dict[str, Any]) -> dict[str, Any]:
    payload = task_payload(task)
    unified_diff = str(payload.get("unified_diff") or "")
    apply_requested = payload.get("apply") is True
    allowed_paths = normalize_relative_paths(payload.get("allowed_paths") or payload.get("paths") or payload.get("files"))
    if not unified_diff.strip():
        raise TaskExecutionError({"code": "MISSING_DIFF", "message": "unified_diff is required"})
    allowed, touched_paths = diff_touches_allowed_paths(unified_diff, allowed_paths)
    if not allowed:
        raise TaskExecutionError(
            {
                "code": "DIFF_PATH_NOT_ALLOWED",
                "message": "diff touches paths outside allowed_paths or allowed_paths is empty",
                "touched_paths": touched_paths,
                "allowed_paths": allowed_paths,
            }
        )

    diff_arg = shell_single_quote(unified_diff)
    check = run_project_command(f"printf %s {diff_arg} | git apply --check -", timeout=30)
    result = {
        "tool": "code.change.apply",
        "mode": "apply" if apply_requested else "dry_run",
        "will_modify_files": apply_requested,
        "touched_paths": touched_paths,
        "allowed_paths": allowed_paths,
        "check_exit_code": check.returncode,
        "check_stdout_tail": check.stdout[-4000:],
        "check_stderr_tail": check.stderr[-4000:],
    }
    if check.returncode != 0:
        raise TaskExecutionError(
            {
                "code": "GIT_APPLY_CHECK_FAILED",
                "message": "git apply --check failed",
                **result,
            }
        )
    if not apply_requested:
        return result

    apply_enabled = os.getenv("CODE_APPLY_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
    approval_hash = str((task.get("metadata") or {}).get("approval_content_hash") or "")
    if not apply_enabled:
        raise TaskExecutionError(
            {"code": "CODE_APPLY_DISABLED", "message": "real code apply is disabled on this worker"}
        )
    if len(approval_hash) != 64:
        raise TaskExecutionError(
            {"code": "APPROVAL_EVIDENCE_REQUIRED", "message": "approved content hash is required for real code apply"}
        )

    applied = run_project_command(f"printf %s {diff_arg} | git apply -", timeout=30)
    result.update(
        {
            "apply_exit_code": applied.returncode,
            "apply_stdout_tail": applied.stdout[-4000:],
            "apply_stderr_tail": applied.stderr[-4000:],
        }
    )
    if applied.returncode != 0:
        raise TaskExecutionError(
            {
                "code": "GIT_APPLY_FAILED",
                "message": "git apply failed",
                **result,
            }
        )
    return result


def run_project_command(
    command: str,
    timeout: int = 30,
    absolute_command: bool = False,
) -> subprocess.CompletedProcess[str]:
    host = os.getenv("PROJECT_CONTEXT_HOST", "192.168.31.31")
    root = os.getenv("PROJECT_CONTEXT_ROOT", "/home/gryps/apps/douyin-listing-workbench")
    path_prefix = os.getenv("PROJECT_COMMAND_PATH_PREFIX", "/home/gryps/.local/bin:$PATH")
    remote_command = command if absolute_command else f"cd {shlex.quote(root)} && {command}"
    remote_command = f"export PATH={path_prefix} && {remote_command}"
    if os.getenv("PROJECT_LOCAL_EXECUTION", "false").lower() in {"1", "true", "yes", "on"}:
        return subprocess.run(
            ["bash", "-lc", remote_command],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    return subprocess.run(
        ["ssh", host, remote_command],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def quality_check_item(name: str, command: str, timeout: int = 30) -> dict[str, Any]:
    completed = run_project_command(command, timeout=timeout)
    return {
        "name": name,
        "command": command,
        "ok": completed.returncode == 0,
        "exit_code": completed.returncode,
        "stdout_tail": completed.stdout[-2000:],
        "stderr_tail": completed.stderr[-2000:],
    }


def check_quality_environment(task: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_task_workspace(task)
    checks = [
        quality_check_item("project_root", "pwd && test -f package.json"),
        quality_check_item("git", "git rev-parse --short HEAD && git status --short | sed -n '1,40p'"),
        quality_check_item("node", "node --version"),
        quality_check_item("npm", "npm --version"),
        quality_check_item("python3", "python3 --version"),
        quality_check_item("project_dependencies", "test -d .venv && echo api_venv:ok; test -d apps/web/node_modules && echo web_node_modules:ok"),
        quality_check_item(
            "npm_scripts",
            "node -e 'const s=require(\"./package.json\").scripts||{}; "
            "const keys=[\"api:lint\",\"api:typecheck\",\"api:test\",\"check\"]; "
            "for (const k of keys) console.log(k + \":\" + (s[k] ? \"ok\" : \"missing\")); "
            "process.exit(keys.every(k=>s[k]) ? 0 : 1);'",
        ),
    ]
    return {
        "tool": "quality.env.check",
        "workspace_id": workspace_id,
        "project_host": os.getenv("PROJECT_CONTEXT_HOST", "192.168.31.31"),
        "project_root": os.getenv("PROJECT_CONTEXT_ROOT", "/home/gryps/apps/douyin-listing-workbench"),
        "passed": all(item["ok"] for item in checks),
        "checks": checks,
        **workcopy_evidence(),
    }


def bootstrap_workspace(task: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_task_workspace(task)
    completed = run_project_command("npm run bootstrap", timeout=int(os.getenv("PROJECT_BOOTSTRAP_TIMEOUT", "1200")))
    result = {
        "tool": "workspace.bootstrap",
        "workspace_id": workspace_id,
        "command": "npm run bootstrap",
        "exit_code": completed.returncode,
        "stdout_tail": completed.stdout[-6000:],
        "stderr_tail": completed.stderr[-6000:],
        "passed": completed.returncode == 0,
        **workcopy_evidence(),
    }
    if completed.returncode != 0:
        raise TaskExecutionError(
            {"code": "WORKSPACE_BOOTSTRAP_FAILED", "message": "npm run bootstrap failed", **result}
        )
    return result


def run_project_test(task: dict[str, Any]) -> dict[str, Any]:
    workspace_id = require_task_workspace(task)
    payload = task.get("input") or {}
    command_key = "check"
    if isinstance(payload, dict):
        command_key = str(payload.get("command") or payload.get("command_key") or command_key)
    commands = allowed_test_commands()
    command = commands.get(command_key)
    if not command:
        raise ValueError(f"unsupported test command: {command_key}")

    completed = run_project_command(command, timeout=int(os.getenv("PROJECT_TEST_TIMEOUT", "300")))
    result = {
        "tool": "test.run",
        "workspace_id": workspace_id,
        "command_key": command_key,
        "command": command,
        "exit_code": completed.returncode,
        "stdout_tail": completed.stdout[-6000:],
        "stderr_tail": completed.stderr[-6000:],
        "passed": completed.returncode == 0,
        **workcopy_evidence(),
    }
    if completed.returncode != 0:
        raise TaskExecutionError(
            {
                "code": "TEST_FAILED",
                "message": f"{command} exited with {completed.returncode}",
                **result,
            }
        )
    return result


async def taskhub_loop() -> None:
    controller_url = os.getenv("CONTROLLER_URL", "").rstrip("/")
    if not controller_url:
        return

    interval = float(os.getenv("TASKHUB_POLL_INTERVAL", "2"))
    project = os.getenv("TASKHUB_PROJECT", "douyin-listing-workbench")
    async with httpx.AsyncClient(timeout=15) as client:
        while True:
            try:
                response = await client.post(
                    f"{controller_url}/taskhub/claim",
                    json={
                        "worker_id": worker_name(),
                        "project": project,
                        "types": supported_task_types(),
                    },
                    headers=taskhub_headers(),
                )
                response.raise_for_status()
                task = response.json().get("task")
                if task:
                    task_id = task["id"]
                    lease_token = task["lease_token"]
                    heartbeat = asyncio.create_task(task_heartbeat_loop(client, controller_url, task_id, lease_token))
                    try:
                        result = await execute_taskhub_task(task)
                        complete = await client.post(
                            f"{controller_url}/taskhub/tasks/{task_id}/complete",
                            json={"worker_id": worker_name(), "lease_token": lease_token, "result": result},
                            headers=taskhub_headers(),
                        )
                        complete.raise_for_status()
                    except TaskExecutionError as exc:
                        archived = archive_task_result(
                            task,
                            {"outcome": "failed", "error": exc.payload},
                        )
                        fail = await client.post(
                            f"{controller_url}/taskhub/tasks/{task_id}/fail",
                            json={
                                "worker_id": worker_name(),
                                "lease_token": lease_token,
                                "error": {**exc.payload, "artifacts": archived["artifacts"]},
                            },
                            headers=taskhub_headers(),
                        )
                        fail.raise_for_status()
                    except Exception as exc:
                        error = {"code": exc.__class__.__name__, "message": str(exc)}
                        archived = archive_task_result(task, {"outcome": "failed", "error": error})
                        fail = await client.post(
                            f"{controller_url}/taskhub/tasks/{task_id}/fail",
                            json={
                                "worker_id": worker_name(),
                                "lease_token": lease_token,
                                "error": {**error, "artifacts": archived["artifacts"]},
                            },
                            headers=taskhub_headers(),
                        )
                        fail.raise_for_status()
                    finally:
                        heartbeat.cancel()
                        try:
                            await heartbeat
                        except asyncio.CancelledError:
                            pass
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            await asyncio.sleep(interval)


async def task_heartbeat_loop(
    client: httpx.AsyncClient,
    controller_url: str,
    task_id: str,
    lease_token: str,
) -> None:
    interval = max(10.0, float(os.getenv("TASKHUB_HEARTBEAT_INTERVAL", "30")))
    while True:
        await asyncio.sleep(interval)
        response = await client.post(
            f"{controller_url}/taskhub/tasks/{task_id}/heartbeat",
            json={"worker_id": worker_name(), "lease_token": lease_token},
            headers=taskhub_headers(),
        )
        response.raise_for_status()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global POLL_TASK
    if taskhub_poll_enabled():
        POLL_TASK = asyncio.create_task(taskhub_loop())
    yield
    if POLL_TASK:
        POLL_TASK.cancel()
        try:
            await POLL_TASK
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Gryps LangGraph Worker", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str | float]:
    controller_url = os.getenv("CONTROLLER_URL", "")
    controller = "not_configured"
    if controller_url:
        try:
            response = httpx.get(f"{controller_url.rstrip('/')}/health", timeout=2)
            controller = "ok" if response.is_success else f"http_{response.status_code}"
        except Exception as exc:
            controller = exc.__class__.__name__

    return {
        "status": "ok",
        "worker": worker_name(),
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "controller": controller,
        "taskhub_poll": str(taskhub_poll_enabled()),
        "task_types": ",".join(supported_task_types()),
        "uptime_seconds": round(time.time() - STARTED_AT, 3),
    }


@app.post("/run")
def run_tool(request: Request, payload: ToolRequest) -> dict[str, str]:
    supplied = request.headers.get("x-taskhub-worker-token", "")
    expected = os.getenv("TASKHUB_WORKER_TOKEN", "")
    if not supplied or not expected or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="worker authentication required")
    return execute_tool(payload.tool, payload.input)
