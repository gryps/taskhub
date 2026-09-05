from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any

from app.egress import ProxyRequiredError, apply_openai_proxy_environment


ACCOUNT_PROVIDERS = {
    "chatgpt_plus_account": {
        "label": "ChatGPT Plus 账号",
        "home_env": "CODEX_PLUS_HOME",
        "default_home": "/home/gryps/.codex-plus",
    },
    "chatgpt_pro_account": {
        "label": "ChatGPT Pro 账号",
        "home_env": "CODEX_PRO_HOME",
        "default_home": "/home/gryps/.codex-pro",
    },
}

PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "risk_level", "tasks", "notes"],
    "properties": {
        "summary": {"type": "string"},
        "risk_level": {"type": "string", "enum": ["low", "medium", "high"]},
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["type", "title", "priority"],
                "properties": {
                    "type": {"type": "string"},
                    "title": {"type": "string"},
                    "priority": {"type": "integer"},
                },
            },
        },
        "notes": {"type": "array", "items": {"type": "string"}},
    },
}

CODE_CHANGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "tests"],
    "properties": {
        "summary": {"type": "string"},
        "tests": {"type": "array", "items": {"type": "string"}},
    },
}

_LOCKS = {provider: threading.Lock() for provider in ACCOUNT_PROVIDERS}
_STATUS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_RUNTIME_STATE: dict[str, dict[str, Any]] = {}


def codex_binary() -> str:
    configured = os.getenv("CODEX_CLI_BIN", "/home/gryps/.local/bin/codex")
    return configured if Path(configured).is_file() else (shutil.which("codex") or configured)


def account_home(provider: str) -> Path:
    definition = ACCOUNT_PROVIDERS[provider]
    return Path(os.getenv(definition["home_env"], definition["default_home"]))


def account_environment(provider: str) -> dict[str, str]:
    env = apply_openai_proxy_environment(dict(os.environ))
    env["CODEX_HOME"] = str(account_home(provider))
    for key in (
        "OPENAI_API_KEY",
        "OPENAI_ACCESS_TOKEN",
        "CODEX_ACCESS_TOKEN",
        "LLM_GPT_API_KEY",
        "LLM_OPENAI_API_KEY",
    ):
        env.pop(key, None)
    return env


def account_login_command(provider: str) -> str:
    if provider not in ACCOUNT_PROVIDERS:
        return ""
    short_name = "plus" if provider == "chatgpt_plus_account" else "pro"
    script = "/home/gryps/apps/langgraph-control/scripts/codex_account_login.sh"
    return f"ssh -p 8022 -t gryps@192.168.31.31 '{script} {short_name}'"


def _classify_failure(text: str) -> tuple[str, str]:
    lowered = text.lower()
    if "invalid_json_schema" in lowered:
        return "invalid_response", "Codex CLI 结构化输出 Schema 无效。"
    if "invalid_request_error" in lowered or "status\": 400" in lowered:
        return "invalid_request", "Codex CLI 请求参数无效。"
    if any(marker in lowered for marker in ("usage limit", "quota", "credits", "limit reached")):
        return "quota_exceeded", "账号额度已用尽或达到使用上限。"
    if "429" in lowered or "rate limit" in lowered:
        return "rate_limited", "账号请求受到速率限制。"
    if any(marker in lowered for marker in ("not logged in", "unauthorized", "401", "sign in", "login")):
        return "needs_reauth", "账号未登录或登录状态已失效。"
    if any(marker in lowered for marker in ("timed out", "timeout", "connection", "network", "dns")):
        return "network_error", "Codex CLI 网络调用失败。"
    return "runner_error", "Codex CLI 执行失败。"


def account_status(provider: str, force: bool = False) -> dict[str, Any]:
    definition = ACCOUNT_PROVIDERS[provider]
    now = time.monotonic()
    cached = _STATUS_CACHE.get(provider)
    if cached and not force and now - cached[0] < 5:
        result = dict(cached[1])
        result["busy"] = _LOCKS[provider].locked()
        if result["busy"] and result.get("configured"):
            result["status"] = "busy"
        return result

    binary = codex_binary()
    home = account_home(provider)
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(home, 0o700)
    base = {
        "provider": provider,
        "label": definition["label"],
        "kind": "account",
        "home": str(home),
        "login_command": account_login_command(provider),
        "connector_implemented": True,
        "status_source": "codex_cli",
        "cli_installed": Path(binary).is_file(),
        "cli_version": "",
        "configured": False,
        "status": "misconfigured",
        "quota_status": "unknown",
        "busy": _LOCKS[provider].locked(),
    }
    if not Path(binary).is_file():
        base["message"] = "Codex CLI 未安装。"
        _STATUS_CACHE[provider] = (now, base)
        return dict(base)
    try:
        version = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=5, check=False)
        base["cli_version"] = version.stdout.strip()[:80]
        completed = subprocess.run(
            [binary, "login", "status"],
            env=account_environment(provider),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if completed.returncode == 0:
            runtime_quota = _RUNTIME_STATE.get(provider, {}).get("quota_status", "available")
            base.update(
                {
                    "authenticated": True,
                    "configured": runtime_quota != "quota_exceeded",
                    "status": "quota_exceeded" if runtime_quota == "quota_exceeded" else ("busy" if base["busy"] else "available"),
                    "quota_status": runtime_quota,
                    "message": "Codex CLI ChatGPT 登录状态可用。",
                }
            )
        else:
            status, message = _classify_failure(f"{completed.stdout}\n{completed.stderr}")
            base.update({"status": status, "message": message})
    except subprocess.TimeoutExpired:
        base.update({"status": "network_error", "message": "Codex CLI 登录状态检查超时。"})
    except ProxyRequiredError:
        base.update({"status": "proxy_required", "message": "OpenAI 账号通道必须配置网络代理。"})
    except OSError:
        base.update({"status": "misconfigured", "message": "Codex CLI 无法启动。"})
    _STATUS_CACHE[provider] = (now, base)
    return dict(base)


def _usage_from_jsonl(output: str) -> dict[str, Any]:
    usage: dict[str, Any] = {}
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and isinstance(event.get("usage"), dict):
            usage = event["usage"]
    return usage


def account_workdir() -> str:
    return os.getenv("CODEX_PROJECT_WORKDIR", "/home/gryps/apps/douyin-listing-workbench-runner")


def run_account(
    provider: str,
    prompt: str,
    model: str | None = None,
    output_schema: dict[str, Any] | None = None,
) -> tuple[str | None, dict[str, Any]]:
    status = account_status(provider, force=True)
    if not status["configured"]:
        return None, {
            "provider": provider,
            "status": "failed",
            "reason": status["status"],
            "message": status.get("message", "账号不可用。"),
        }
    lock = _LOCKS[provider]
    if not lock.acquire(blocking=False):
        return None, {"provider": provider, "status": "failed", "reason": "busy", "message": "账号 Runner 正在执行其他任务。"}

    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix=f"codex-{provider}-") as temp_dir:
            schema_path = Path(temp_dir) / "schema.json"
            output_path = Path(temp_dir) / "last-message.json"
            schema_path.write_text(json.dumps(output_schema or PLAN_SCHEMA), encoding="utf-8")
            command = [
                codex_binary(),
                "exec",
                "--ephemeral",
                "--ignore-user-config",
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                "--color",
                "never",
                "--cd",
                account_workdir(),
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                "--json",
            ]
            if model:
                command.extend(["--model", model])
            command.append("-")
            completed = subprocess.run(
                command,
                input=prompt,
                env=account_environment(provider),
                capture_output=True,
                text=True,
                timeout=float(os.getenv("CODEX_RUNNER_TIMEOUT", "600")),
                check=False,
            )
            duration_ms = int((time.monotonic() - started) * 1000)
            if completed.returncode != 0 or not output_path.exists():
                reason, message = _classify_failure(f"{completed.stdout[-2000:]}\n{completed.stderr[-2000:]}")
                _RUNTIME_STATE[provider] = {"quota_status": "quota_exceeded" if reason == "quota_exceeded" else "unknown"}
                _STATUS_CACHE.pop(provider, None)
                return None, {
                    "provider": provider,
                    "status": "failed",
                    "reason": reason,
                    "message": message,
                    "exit_code": completed.returncode,
                    "duration_ms": duration_ms,
                    "model": model or "account_default",
                }
            content = output_path.read_text(encoding="utf-8")
            _RUNTIME_STATE[provider] = {"quota_status": "available"}
            _STATUS_CACHE.pop(provider, None)
            return content, {
                "provider": provider,
                "status": "succeeded",
                "reason": "ok",
                "duration_ms": duration_ms,
                "model": model or "account_default",
                "usage": _usage_from_jsonl(completed.stdout),
            }
    except subprocess.TimeoutExpired:
        _STATUS_CACHE.pop(provider, None)
        return None, {
            "provider": provider,
            "status": "failed",
            "reason": "timeout",
            "message": "Codex CLI Runner 执行超时。",
            "duration_ms": int((time.monotonic() - started) * 1000),
            "model": model or "account_default",
        }
    except ProxyRequiredError:
        _STATUS_CACHE.pop(provider, None)
        return None, {
            "provider": provider,
            "status": "failed",
            "reason": "proxy_required",
            "message": "OpenAI 账号通道必须配置网络代理。",
            "duration_ms": int((time.monotonic() - started) * 1000),
            "model": model or "account_default",
        }
    except (OSError, ValueError) as exc:
        _STATUS_CACHE.pop(provider, None)
        return None, {
            "provider": provider,
            "status": "failed",
            "reason": "runner_error",
            "message": f"Codex CLI Runner 无法执行: {exc.__class__.__name__}",
            "duration_ms": int((time.monotonic() - started) * 1000),
            "model": model or "account_default",
        }
    finally:
        lock.release()


def run_account_workspace(
    provider: str,
    prompt: str,
    workdir: str,
    model: str | None = None,
) -> tuple[str | None, dict[str, Any]]:
    """Run an authenticated Codex account against an isolated controller-side copy."""
    status = account_status(provider, force=True)
    if not status["configured"]:
        return None, {
            "provider": provider,
            "status": "failed",
            "reason": status["status"],
            "message": status.get("message", "账号不可用。"),
        }
    lock = _LOCKS[provider]
    if not lock.acquire(blocking=False):
        return None, {"provider": provider, "status": "failed", "reason": "busy", "message": "账号 Runner 正在执行其他任务。"}

    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix=f"codex-result-{provider}-") as temp_dir:
            schema_path = Path(temp_dir) / "schema.json"
            output_path = Path(temp_dir) / "last-message.json"
            schema_path.write_text(json.dumps(CODE_CHANGE_SCHEMA), encoding="utf-8")
            command = [
                codex_binary(),
                "exec",
                "--ephemeral",
                "--ignore-user-config",
                "--sandbox",
                "workspace-write",
                "--skip-git-repo-check",
                "--color",
                "never",
                "--cd",
                workdir,
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                "--json",
            ]
            if model:
                command.extend(["--model", model])
            command.append("-")
            completed = subprocess.run(
                command,
                input=prompt,
                env=account_environment(provider),
                capture_output=True,
                text=True,
                timeout=float(os.getenv("CODEX_CODER_TIMEOUT", "1200")),
                check=False,
            )
            duration_ms = int((time.monotonic() - started) * 1000)
            if completed.returncode != 0 or not output_path.exists():
                reason, message = _classify_failure(f"{completed.stdout[-2000:]}\n{completed.stderr[-2000:]}")
                return None, {
                    "provider": provider,
                    "status": "failed",
                    "reason": reason,
                    "message": message,
                    "exit_code": completed.returncode,
                    "duration_ms": duration_ms,
                    "model": model or "account_default",
                }
            _RUNTIME_STATE[provider] = {"quota_status": "available"}
            return output_path.read_text(encoding="utf-8"), {
                "provider": provider,
                "status": "succeeded",
                "reason": "ok",
                "duration_ms": duration_ms,
                "model": model or "account_default",
                "usage": _usage_from_jsonl(completed.stdout),
            }
    except subprocess.TimeoutExpired:
        return None, {
            "provider": provider,
            "status": "failed",
            "reason": "timeout",
            "message": "Codex 施工执行超时。",
            "duration_ms": int((time.monotonic() - started) * 1000),
            "model": model or "account_default",
        }
    except ProxyRequiredError:
        return None, {
            "provider": provider,
            "status": "failed",
            "reason": "proxy_required",
            "message": "OpenAI 账号施工通道必须配置网络代理。",
            "duration_ms": int((time.monotonic() - started) * 1000),
            "model": model or "account_default",
        }
    except (OSError, ValueError) as exc:
        return None, {
            "provider": provider,
            "status": "failed",
            "reason": "runner_error",
            "message": f"Codex 施工执行失败: {exc.__class__.__name__}",
            "duration_ms": int((time.monotonic() - started) * 1000),
            "model": model or "account_default",
        }
    finally:
        lock.release()
