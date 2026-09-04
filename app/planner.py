from __future__ import annotations

import json
import os
import re
import subprocess
import stat
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter
import httpx
from pydantic import BaseModel, Field

from app.taskhub import TaskCreate, create_task_graph, create_workflow_evidence
from app.project_snapshot import capture_project_snapshot
from app.account_runner import ACCOUNT_PROVIDERS, account_status, run_account
from app.egress import ProxyRequiredError, openai_proxy_required, provider_http_client
from app.provider_health import before_attempt, provider_health, record_failure, record_success


router = APIRouter(prefix="/planner", tags=["planner"])

ALLOWED_PLANNER_TASK_TYPES = {
    "code.change",
    "code.diff.preview",
    "ops.note",
    "project.context.sync",
    "project.git.status",
    "quality.env.check",
    "requirement.split",
    "test.run",
    "h5.inspect",
}

DEFAULT_PROVIDER_ORDER = ["openai", "deepseek", "minimax"]
ROLE_EXECUTION_ORDER = ["planner", "coder", "reviewer", "risk", "supervisor"]

ROLE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "coder": {
        "label": "施工",
        "priority": 1,
        "default_model": "gpt5.6sol",
        "default_providers": ["chatgpt_plus_account", "chatgpt_pro_account", "gpt_api"],
    },
    "supervisor": {
        "label": "监督",
        "priority": 2,
        "default_model": "gpt5.6sol",
        "default_providers": ["chatgpt_plus_account", "chatgpt_pro_account", "gpt_api"],
    },
    "planner": {
        "label": "规划",
        "priority": 3,
        "default_model": "gpt5.6terra",
        "default_providers": ["chatgpt_plus_account", "chatgpt_pro_account", "gpt_api"],
    },
    "risk": {
        "label": "风险",
        "priority": 4,
        "default_model": "minimax-m3",
        "default_providers": ["minimax_api"],
    },
    "reviewer": {
        "label": "审查",
        "priority": 5,
        "default_model": "deepseekv4pro",
        "default_providers": ["deepseek_api"],
    },
}

PROVIDER_DEFINITIONS: dict[str, dict[str, str]] = {
    "chatgpt_plus_account": {"label": "ChatGPT Plus 账号", "kind": "account"},
    "chatgpt_pro_account": {"label": "ChatGPT Pro 账号", "kind": "account"},
    "gpt_api": {"label": "GPT API", "kind": "api", "env_prefix": "GPT"},
    "openai": {"label": "OpenAI API", "kind": "api", "env_prefix": "OPENAI"},
    "deepseek_api": {"label": "DeepSeek API", "kind": "api", "env_prefix": "DEEPSEEK"},
    "deepseek": {"label": "DeepSeek API", "kind": "api", "env_prefix": "DEEPSEEK"},
    "minimax_api": {"label": "MiniMax API", "kind": "api", "env_prefix": "MINIMAX"},
    "minimax": {"label": "MiniMax API", "kind": "api", "env_prefix": "MINIMAX"},
}

DEFAULT_API_CONFIG = {
    "GPT": {"base_url": "https://api.openai.com/v1", "model": "gpt-4.1-mini"},
    "OPENAI": {"base_url": "https://api.openai.com/v1", "model": "gpt-4.1-mini"},
    "DEEPSEEK": {"base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
    "MINIMAX": {"base_url": "https://api.minimax.io/v1", "model": "MiniMax-M1"},
}


class PlannerRequest(BaseModel):
    requirement: str = Field(..., min_length=1)
    project: str = Field("douyin-listing-workbench", min_length=1)
    title: str | None = None
    priority: int = 70
    provider: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=160)
    pipeline_id: uuid.UUID | None = None


class ProviderTestRequest(BaseModel):
    role: str = Field("planner", min_length=1)
    prompt: str = Field("Return a JSON object with summary, risk_level, tasks, and notes.", min_length=1)


class ProviderConfigRequest(BaseModel):
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    role_models: dict[str, str] = Field(default_factory=dict)


class ProxyConfigRequest(BaseModel):
    enabled: bool = False
    proxy_url: str | None = None
    no_proxy: str | None = None
    restart: bool = False


class AccountProviderConfigRequest(BaseModel):
    status: str = Field("not_connected", min_length=1)
    quota_status: str = Field("unknown", min_length=1)
    restart: bool = False


def env_key(provider: str, key: str) -> str:
    return f"LLM_{provider.upper()}_{key}"


def api_env_key(provider: str, key: str) -> str:
    prefix = PROVIDER_DEFINITIONS.get(provider, {}).get("env_prefix", provider.upper())
    return f"LLM_{prefix}_{key}"


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * 8}{value[-4:]}"


def mask_proxy_url(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if not parsed.password:
        return value
    username = parsed.username or ""
    hostname = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    auth = f"{username}:********@" if username else "********@"
    return urlunsplit((parsed.scheme, f"{auth}{hostname}{port}", parsed.path, parsed.query, parsed.fragment))


def proxy_config() -> dict[str, Any]:
    proxy_url = os.getenv("OPENAI_PROXY_URL") or os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or os.getenv("ALL_PROXY") or ""
    no_proxy = os.getenv("NO_PROXY", "")
    return {
        "enabled": bool(proxy_url),
        "proxy_url_mask": mask_proxy_url(proxy_url),
        "http_proxy_set": bool(os.getenv("HTTP_PROXY")),
        "https_proxy_set": bool(os.getenv("HTTPS_PROXY")),
        "all_proxy_set": bool(os.getenv("ALL_PROXY")),
        "openai_proxy_set": bool(os.getenv("OPENAI_PROXY_URL")),
        "no_proxy": no_proxy,
        "restart_required": False,
        "scope": "openai_only",
        "required": openai_proxy_required(),
        "direct_providers": ["deepseek_api", "minimax_api"],
    }


def schedule_control_restart() -> dict[str, Any]:
    try:
        subprocess.Popen(
            ["bash", "-lc", "sleep 1; systemctl --user restart langgraph-control.service"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return {"scheduled": True, "command": "systemctl --user restart langgraph-control.service"}
    except Exception as exc:
        return {"scheduled": False, "error": exc.__class__.__name__}


def provider_config(provider: str) -> dict[str, Any]:
    definition = PROVIDER_DEFINITIONS.get(provider, {"label": provider, "kind": "api", "env_prefix": provider.upper()})
    kind = definition["kind"]
    if kind == "account":
        return {**account_status(provider), "model": "account_default"}

    prefix = definition.get("env_prefix", provider.upper())
    defaults = DEFAULT_API_CONFIG.get(prefix, {"base_url": "", "model": ""})
    api_key = os.getenv(api_env_key(provider, "API_KEY"), "")
    base_url = os.getenv(api_env_key(provider, "BASE_URL"), defaults["base_url"])
    model = os.getenv(api_env_key(provider, "MODEL"), defaults["model"])
    return {
        "provider": provider,
        "label": definition["label"],
        "kind": kind,
        "configured": bool(api_key),
        "status": "configured" if api_key else "not_configured",
        "api_key_mask": mask_secret(api_key),
        "quota_status": os.getenv(f"{api_env_key(provider, 'QUOTA_STATUS')}", "unknown"),
        "base_url": base_url,
        "model": model,
    }


def provider_model_for_role(provider: str, role: dict[str, Any]) -> str:
    if provider_config(provider)["kind"] == "account":
        key = f"ROLE_{str(role['role']).upper()}_{provider.upper()}_MODEL"
        return os.getenv(key, "").strip() or "account_default"
    role_key = str(role["role"]).upper()
    provider_key = provider.upper()
    prefix = PROVIDER_DEFINITIONS.get(provider, {}).get("env_prefix", provider.upper())
    return (
        os.getenv(f"ROLE_{role_key}_{provider_key}_MODEL")
        or os.getenv(f"ROLE_{role_key}_{prefix}_MODEL")
        or str(provider_config(provider).get("model") or "")
        or str(role["model"])
    )


def chat_completion_payload(model: str, messages: list[dict[str, str]], max_tokens: int | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "response_format": {"type": "json_object"},
    }
    if model.startswith("gpt-5") or model.startswith("o"):
        if max_tokens is not None:
            payload["max_completion_tokens"] = max_tokens
        return payload
    payload["temperature"] = 0.2
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    return payload


def env_file_status() -> dict[str, Any]:
    path = os.getenv("LANGGRAPH_ENV_FILE", "/home/gryps/apps/langgraph-control/.env")
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
        return {
            "path": path,
            "exists": True,
            "mode": oct(mode),
            "secure": mode & 0o077 == 0,
        }
    except FileNotFoundError:
        return {"path": path, "exists": False, "mode": None, "secure": False}


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def write_env_file(path: Path, values: dict[str, str]) -> str | None:
    backup_path = None
    if path.exists():
        backup_path = f"{path}.backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        Path(backup_path).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        os.chmod(backup_path, 0o600)
    lines = ["# Managed by LangGraph control. Do not commit this file.", ""]
    for key in sorted(values):
        lines.append(f"{key}={values[key]}")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return backup_path


def provider_required_env(provider: str) -> list[str]:
    definition = PROVIDER_DEFINITIONS.get(provider)
    if not definition:
        return []
    if definition["kind"] == "account":
        return [f"{provider.upper()}_STATUS", f"{provider.upper()}_QUOTA_STATUS"]
    prefix = definition.get("env_prefix", provider.upper())
    return [f"LLM_{prefix}_API_KEY", f"LLM_{prefix}_BASE_URL", f"LLM_{prefix}_MODEL"]


def account_provider_items() -> list[dict[str, Any]]:
    return [provider_config(provider) for provider in ["chatgpt_plus_account", "chatgpt_pro_account"]]


def provider_role_model_env(provider: str) -> dict[str, str]:
    if provider == "gpt_api":
        return {
            "coder": "ROLE_CODER_GPT_API_MODEL",
            "supervisor": "ROLE_SUPERVISOR_GPT_API_MODEL",
            "planner": "ROLE_PLANNER_GPT_API_MODEL",
        }
    if provider == "deepseek_api":
        return {"reviewer": "ROLE_REVIEWER_DEEPSEEK_API_MODEL"}
    if provider == "minimax_api":
        return {"risk": "ROLE_RISK_MINIMAX_API_MODEL"}
    return {}


def role_model_override(provider: str, role: str) -> str:
    env_name = provider_role_model_env(provider).get(role)
    if not env_name:
        return ""
    return os.getenv(env_name, "")


@router.get("/config-check")
def config_check() -> dict[str, Any]:
    api_providers = ["gpt_api", "deepseek_api", "minimax_api"]
    provider_items = []
    for provider in api_providers:
        config = provider_config(provider)
        required = provider_required_env(provider)
        missing = [name for name in required if name.endswith("_API_KEY") and not os.getenv(name)]
        provider_items.append(
            {
                "provider": provider,
                "label": config["label"],
                "configured": config["configured"],
                "status": config["status"],
                "api_key_mask": config.get("api_key_mask", ""),
                "base_url": config.get("base_url"),
                "model": config.get("model"),
                "required_env": required,
                "missing_env": missing,
            }
        )

    role_items = []
    for role_name in sorted(ROLE_DEFINITIONS, key=lambda item: ROLE_DEFINITIONS[item]["priority"]):
        role = role_config(role_name)
        missing_provider = not any(provider["configured"] for provider in role["providers"])
        role_items.append(
            {
                "role": role_name,
                "label": role["label"],
                "model_label": role["model"],
                "provider_order": role["provider_order"],
                "provider_models": [
                    {
                        "provider": provider["provider"],
                        "resolved_model": provider.get("resolved_model"),
                        "override_model": role_model_override(provider["provider"], role_name),
                    }
                    for provider in role["providers"]
                ],
                "ready": not missing_provider,
                "missing_provider": missing_provider,
            }
        )

    env_status = env_file_status()
    alerts = []
    if not env_status["exists"]:
        alerts.append({"code": "ENV_FILE_MISSING", "level": "warning", "message": f"{env_status['path']} 不存在。"})
    elif not env_status["secure"]:
        alerts.append({"code": "ENV_FILE_PERMISSIONS", "level": "warning", "message": f"{env_status['path']} 权限不是 0600。"})
    for provider in provider_items:
        if not provider["configured"]:
            alerts.append({"code": "PROVIDER_NOT_CONFIGURED", "level": "info", "message": f"{provider['label']} 未配置 API key。"})

    return {
        "env_file": env_status,
        "proxy": proxy_config(),
        "account_providers": account_provider_items(),
        "providers": provider_items,
        "roles": role_items,
        "alerts": alerts,
        "configure_script": "/home/gryps/apps/langgraph-control/scripts/configure_llm_provider.py",
        "restart_command": "systemctl --user restart langgraph-control.service",
    }


@router.get("/account-providers/config")
def get_account_provider_config() -> dict[str, Any]:
    return {
        "ok": True,
        "providers": account_provider_items(),
        "read_only": True,
        "message": "账号状态由隔离的 Codex CLI 登录目录自动探测，不允许手工设置。",
    }


@router.post("/account-providers/{provider}/config")
def save_account_provider_config(provider: str, request: AccountProviderConfigRequest) -> dict[str, Any]:
    return {
        "provider": provider,
        "ok": False,
        "status": "read_only",
        "config": provider_config(provider) if provider in ACCOUNT_PROVIDERS else None,
        "message": "账号状态由 Codex CLI 自动探测，请使用页面显示的 device-auth 命令登录。",
    }


@router.get("/proxy/config")
def get_proxy_config() -> dict[str, Any]:
    return {
        "ok": True,
        "proxy": proxy_config(),
        "env_file": env_file_status(),
        "restart_command": "systemctl --user restart langgraph-control.service",
    }


@router.post("/proxy/config")
def save_proxy_config(request: ProxyConfigRequest) -> dict[str, Any]:
    if openai_proxy_required() and not request.enabled:
        return {
            "ok": False,
            "status": "proxy_required",
            "message": "OpenAI 模型网络代理为项目强制策略，不能关闭。",
            "proxy": proxy_config(),
        }
    env_path = Path(os.getenv("LANGGRAPH_ENV_FILE", "/home/gryps/apps/langgraph-control/.env"))
    values = read_env_file(env_path)
    updated_keys: list[str] = []
    proxy_keys = ["OPENAI_PROXY_URL", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]
    no_proxy_keys = ["NO_PROXY", "no_proxy"]

    if request.enabled:
        proxy_url = (request.proxy_url or "").strip() or values.get("OPENAI_PROXY_URL") or values.get("HTTPS_PROXY") or values.get("HTTP_PROXY") or values.get("ALL_PROXY") or ""
        if not proxy_url:
            return {
                "ok": False,
                "status": "invalid_proxy",
                "message": "启用代理时必须填写代理地址。",
                "proxy": proxy_config(),
            }
        for key in ["OPENAI_PROXY_URL", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"]:
            if values.get(key) != proxy_url:
                values[key] = proxy_url
                updated_keys.append(key)
            os.environ[key] = proxy_url
        for key in ["http_proxy", "https_proxy", "all_proxy"]:
            values.pop(key, None)
            os.environ.pop(key, None)
        no_proxy = (request.no_proxy or "127.0.0.1,localhost,192.168.31.0/24,192.168.31.24,192.168.31.31").strip()
        for key in ["NO_PROXY", "no_proxy"]:
            if values.get(key) != no_proxy:
                values[key] = no_proxy
                updated_keys.append(key)
            os.environ[key] = no_proxy
    else:
        for key in proxy_keys + no_proxy_keys:
            if key in values:
                values.pop(key, None)
                updated_keys.append(key)
            os.environ.pop(key, None)

    backup_path = write_env_file(env_path, values)
    restart = schedule_control_restart() if request.restart else {"scheduled": False}
    return {
        "ok": True,
        "status": "saved",
        "proxy": proxy_config(),
        "updated_keys": sorted(set(updated_keys)),
        "env_file": env_file_status(),
        "backup_path": backup_path,
        "restart": restart,
        "message": "代理配置已保存。" + (" 控制服务正在重启。" if request.restart else " 重启后对服务进程完全生效。"),
    }


@router.post("/providers/{provider}/config")
def save_provider_config(provider: str, request: ProviderConfigRequest) -> dict[str, Any]:
    if provider not in {"gpt_api", "deepseek_api", "minimax_api"}:
        return {
            "provider": provider,
            "ok": False,
            "status": "unsupported_provider",
            "message": "只允许在页面配置 API 型 provider。",
        }

    definition = PROVIDER_DEFINITIONS[provider]
    prefix = definition["env_prefix"]
    env_path = Path(os.getenv("LANGGRAPH_ENV_FILE", "/home/gryps/apps/langgraph-control/.env"))
    values = read_env_file(env_path)
    defaults = DEFAULT_API_CONFIG[prefix]
    updated_keys: list[str] = []

    updates = {
        f"LLM_{prefix}_BASE_URL": (request.base_url or defaults["base_url"]).strip(),
        f"LLM_{prefix}_MODEL": (request.model or defaults["model"]).strip(),
        f"LLM_{prefix}_QUOTA_STATUS": values.get(f"LLM_{prefix}_QUOTA_STATUS", "unknown") or "unknown",
    }
    if request.api_key and request.api_key.strip():
        updates[f"LLM_{prefix}_API_KEY"] = request.api_key.strip()

    for key, value in updates.items():
        if value and values.get(key) != value:
            values[key] = value
            updated_keys.append(key)
            os.environ[key] = value

    allowed_role_model_env = provider_role_model_env(provider)
    for role, env_name in allowed_role_model_env.items():
        raw_value = request.role_models.get(role)
        if raw_value is None:
            continue
        value = raw_value.strip()
        if value and values.get(env_name) != value:
            values[env_name] = value
            updated_keys.append(env_name)
            os.environ[env_name] = value
        if not value and env_name in values:
            values.pop(env_name, None)
            updated_keys.append(env_name)
            os.environ.pop(env_name, None)

    backup_path = write_env_file(env_path, values)
    config = provider_config(provider)
    return {
        "provider": provider,
        "label": config["label"],
        "ok": True,
        "status": config["status"],
        "configured": config["configured"],
        "api_key_mask": config.get("api_key_mask", ""),
        "base_url": config.get("base_url"),
        "model": config.get("model"),
        "saved_base_url": os.getenv(f"LLM_{prefix}_BASE_URL", ""),
        "saved_model": os.getenv(f"LLM_{prefix}_MODEL", ""),
        "role_models": {
            role: os.getenv(env_name, "")
            for role, env_name in allowed_role_model_env.items()
        },
        "updated_keys": updated_keys,
        "env_file": env_file_status(),
        "backup_path": backup_path,
        "message": "Provider 配置已保存，当前进程已加载；必要时可重启服务确认持久化。",
    }


@router.get("/providers")
def providers() -> dict[str, Any]:
    selected = os.getenv("LLM_PLANNER_PROVIDER", "")
    return {
        "selected": selected or None,
        "providers": [provider_config(provider) for provider in ["chatgpt_plus_account", "chatgpt_pro_account", "gpt_api", "deepseek_api", "minimax_api"]],
        "fallback": "plus -> pro -> api",
        "recovery_policy": "retry preferred provider after cooldown and fail back immediately on success",
    }


@router.get("/providers/health")
def providers_health() -> dict[str, Any]:
    return {
        "cooldown_seconds": int(os.getenv("PROVIDER_COOLDOWN_SECONDS", "60")),
        "providers": provider_health(),
    }


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def role_config(role: str) -> dict[str, Any]:
    definition = ROLE_DEFINITIONS[role]
    env_name = f"ROLE_{role.upper()}"
    model = os.getenv(f"{env_name}_MODEL", definition["default_model"])
    providers_order = split_csv(os.getenv(f"{env_name}_PROVIDERS", ",".join(definition["default_providers"])))
    configured_providers = []
    for provider in providers_order:
        config = provider_config(provider)
        config["resolved_model"] = provider_model_for_role(provider, {"role": role, "model": model})
        configured_providers.append(config)
    return {
        "role": role,
        "label": definition["label"],
        "priority": definition["priority"],
        "model": model,
        "providers": configured_providers,
        "provider_order": providers_order,
    }


@router.get("/roles")
def roles() -> dict[str, Any]:
    role_items = sorted((role_config(role) for role in ROLE_DEFINITIONS), key=lambda item: item["priority"])
    alerts = []
    for role in role_items:
        if not any(provider["configured"] for provider in role["providers"]):
            alerts.append(
                {
                    "role": role["role"],
                    "code": "NO_CONFIGURED_PROVIDER",
                    "level": "warning",
                    "message": f"{role['label']}没有已连接或已配置的 provider，会进入规则 fallback/人工接入。",
                }
            )
    return {
        "roles": role_items,
        "execution_order": ROLE_EXECUTION_ORDER,
        "alerts": alerts,
    }


def title_with(prefix: str, title: str, max_length: int = 96) -> str:
    return f"{prefix}: {title}"[:max_length]


def fallback_tasks(request: PlannerRequest, reason: str) -> list[dict[str, Any]]:
    title = request.title or request.requirement[:40] or "新的项目需求"
    return [
        {
            "type": "quality.env.check",
            "title": title_with("环境检测", title),
            "priority": request.priority - 2,
            "input": {},
            "metadata": {"stage": "planner_environment_gate"},
        },
        {
            "type": "code.change",
            "title": title_with("代码变更计划", title),
            "priority": request.priority - 5,
            "input": {
                "requirement": request.requirement,
                "mode": "plan_only",
                "constraints": [
                    "中级 planner 只生成受控任务计划。",
                    "真实写入必须经过 review.human 审批。",
                    "不得触碰密钥、Cookie、Token、登录态和生产数据。",
                    f"fallback 原因: {reason}",
                ],
            },
            "metadata": {"stage": "planner_code_plan"},
        },
        {
            "type": "code.diff.preview",
            "title": title_with("diff 预览", title),
            "priority": request.priority - 8,
            "input": {"paths": []},
            "metadata": {"stage": "planner_diff_preview"},
        },
        {
            "type": "test.run",
            "title": title_with("API lint", title),
            "priority": request.priority - 10,
            "input": {"command": "api.lint"},
            "metadata": {"stage": "planner_quality_gate"},
        },
    ]


def fallback_plan(request: PlannerRequest, reason: str) -> dict[str, Any]:
    return {
        "source": "fallback",
        "reason": reason,
        "summary": request.requirement,
        "risk_level": "medium",
        "tasks": fallback_tasks(request, reason),
    }


def role_prompt(role: dict[str, Any], request: PlannerRequest, prior_outputs: list[dict[str, Any]]) -> list[dict[str, str]]:
    allowed = ", ".join(sorted(ALLOWED_PLANNER_TASK_TYPES))
    role_name = f"{role['label']}({role['role']})"
    prior = json.dumps(prior_outputs[-3:], ensure_ascii=False)[:6000]
    system = (
        "You are one role in a LangGraph multi-agent TaskHub. "
        "Return only valid JSON. Do not include markdown. "
        f"Current role: {role_name}. Model label: {role['model']}. "
        f"Allowed task types: {allowed}. "
        "Never plan production publishing, credential access, cookies, tokens, or destructive actions. "
        "Prefer reviewable plan-only tasks. "
        "Schema: {\"summary\": string, \"risk_level\": \"low|medium|high\", "
        "\"tasks\": [{\"type\": string, \"title\": string, \"priority\": number, "
        "\"input\": object, \"metadata\": object}], \"notes\": [string]}"
    )
    user = (
        f"Project: {request.project}\n"
        f"Title: {request.title or ''}\n"
        f"Priority: {request.priority}\n"
        f"Requirement:\n{request.requirement}\n\n"
        f"Prior role outputs:\n{prior}\n"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def planner_prompt(request: PlannerRequest) -> list[dict[str, str]]:
    return role_prompt(role_config("planner"), request, [])


def extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
    fence_match = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fence_match:
        text = fence_match.group(1).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("planner response must be a JSON object")
    return value


def classify_http_error(status_code: int, body: str) -> tuple[str, str]:
    lower = body.lower()
    if status_code in {401, 403}:
        return "auth_failed", "API 鉴权失败，请检查 key、base_url 或账号权限。"
    if status_code == 429:
        if any(word in lower for word in ["quota", "insufficient", "billing", "exceeded", "余额", "额度", "用尽"]):
            return "quota_exceeded", "API 额度可能用尽或余额不足，需要切换 provider 或充值。"
        return "rate_limited", "API 触发限流，需要稍后重试或切换 provider。"
    if 500 <= status_code:
        return "provider_unavailable", "Provider 服务端异常。"
    return "http_error", f"Provider 返回 HTTP {status_code}。"


def alert(role: str, provider: str, code: str, message: str, level: str = "warning") -> dict[str, str]:
    return {
        "role": role,
        "provider": provider,
        "code": code.upper(),
        "level": level,
        "message": message,
    }


def failed_attempt(role: str, provider: str, reason: str, message: str, level: str = "warning") -> dict[str, Any]:
    return {
        "role": role,
        "provider": provider,
        "status": "failed",
        "reason": reason,
        "alert": alert(role, provider, reason, message, level),
    }


def call_account_provider(
    role: dict[str, Any],
    provider: str,
    request: PlannerRequest,
    prior_outputs: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    messages = role_prompt(role, request, prior_outputs)
    prompt = "\n\n".join(f"{item['role'].upper()}:\n{item['content']}" for item in messages)
    model = provider_model_for_role(provider, role)
    content, attempt = run_account(provider, prompt, None if model == "account_default" else model)
    attempt.update({"role": role["role"], "provider": provider})
    if content is None:
        reason = str(attempt.get("reason") or "runner_error")
        message = str(attempt.pop("message", "Codex CLI Runner 调用失败。"))
        attempt["alert"] = alert(role["role"], provider, reason, message)
        return None, attempt
    try:
        raw_plan = extract_json_object(content)
    except (json.JSONDecodeError, ValueError, TypeError):
        return None, failed_attempt(role["role"], provider, "invalid_response", "Codex CLI 返回的结构化结果无效。")
    raw_plan["source"] = provider
    raw_plan["model"] = attempt.get("model", model)
    return raw_plan, attempt


def call_api_provider(provider: str, role: dict[str, Any], request: PlannerRequest, prior_outputs: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    config = provider_config(provider)
    api_key = os.getenv(api_env_key(provider, "API_KEY"), "")
    if not api_key:
        return None, failed_attempt(role["role"], provider, "not_configured", f"{config['label']}没有配置 API key。", "info")
    base_url = str(config["base_url"]).rstrip("/")
    model = provider_model_for_role(provider, role)
    timeout = float(os.getenv("LLM_PLANNER_TIMEOUT", "60"))
    try:
        with provider_http_client(provider, timeout) as client:
            response = client.post(
                f"{base_url}/chat/completions",
                headers={"authorization": f"Bearer {api_key}", "content-type": "application/json"},
                json=chat_completion_payload(model, role_prompt(role, request, prior_outputs)),
            )
        if response.status_code >= 400:
            reason, message = classify_http_error(response.status_code, response.text[:1000])
            return None, failed_attempt(role["role"], provider, reason, message)
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        raw_plan = extract_json_object(content)
        raw_plan["source"] = provider
        raw_plan["model"] = model
        attempt = {
            "role": role["role"],
            "provider": provider,
            "status": "succeeded",
            "model": model,
        }
        return raw_plan, attempt
    except httpx.TimeoutException:
        return None, failed_attempt(role["role"], provider, "timeout", f"{config['label']}调用超时。")
    except ProxyRequiredError:
        return None, failed_attempt(role["role"], provider, "proxy_required", "OpenAI API 必须配置网络代理。")
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        return None, failed_attempt(role["role"], provider, "invalid_response", f"{config['label']}返回格式无效: {exc.__class__.__name__}")
    except Exception as exc:
        return None, failed_attempt(role["role"], provider, "provider_error", f"{config['label']}调用失败: {exc.__class__.__name__}")


def test_api_provider(provider: str, role: dict[str, Any], prompt: str) -> dict[str, Any]:
    config = provider_config(provider)
    api_key = os.getenv(api_env_key(provider, "API_KEY"), "")
    if not api_key:
        return {
            "provider": provider,
            "label": config["label"],
            "kind": config["kind"],
            "configured": False,
            "ok": False,
            "status": "not_configured",
            "reason": "not_configured",
            "message": f"{config['label']}没有配置 API key。",
            "alert": alert(role["role"], provider, "not_configured", f"{config['label']}没有配置 API key。", "info"),
        }

    base_url = str(config["base_url"]).rstrip("/")
    model = provider_model_for_role(provider, role)
    timeout = float(os.getenv("LLM_PROVIDER_TEST_TIMEOUT", os.getenv("LLM_PLANNER_TIMEOUT", "60")))
    messages = [
        {
            "role": "system",
            "content": (
                "Return only JSON. Schema: "
                "{\"summary\": string, \"risk_level\": \"low|medium|high\", \"tasks\": [], \"notes\": [string]}"
            ),
        },
        {"role": "user", "content": prompt},
    ]
    try:
        with provider_http_client(provider, timeout) as client:
            response = client.post(
                f"{base_url}/chat/completions",
                headers={"authorization": f"Bearer {api_key}", "content-type": "application/json"},
                json=chat_completion_payload(model, messages, max_tokens=1024),
            )
        if response.status_code >= 400:
            reason, message = classify_http_error(response.status_code, response.text[:1000])
            return {
                "provider": provider,
                "label": config["label"],
                "kind": config["kind"],
                "configured": True,
                "ok": False,
                "status": reason,
                "reason": reason,
                "message": message,
                "http_status": response.status_code,
                "model": model,
                "alert": alert(role["role"], provider, reason, message),
            }
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        parsed = extract_json_object(content)
        return {
            "provider": provider,
            "label": config["label"],
            "kind": config["kind"],
            "configured": True,
            "ok": True,
            "status": "ok",
            "reason": "ok",
            "message": "Provider 连通性测试通过。",
            "model": model,
            "usage": payload.get("usage", {}),
            "sample": {
                "summary": parsed.get("summary"),
                "risk_level": parsed.get("risk_level"),
                "notes": parsed.get("notes", []),
            },
        }
    except httpx.TimeoutException:
        message = f"{config['label']}连通性测试超时。"
        return {
            "provider": provider,
            "label": config["label"],
            "kind": config["kind"],
            "configured": True,
            "ok": False,
            "status": "timeout",
            "reason": "timeout",
            "message": message,
            "model": model,
            "alert": alert(role["role"], provider, "timeout", message),
        }
    except ProxyRequiredError:
        message = "OpenAI API 必须配置网络代理。"
        return {
            "provider": provider,
            "label": config["label"],
            "kind": config["kind"],
            "configured": True,
            "ok": False,
            "status": "proxy_required",
            "reason": "proxy_required",
            "message": message,
            "model": model,
            "alert": alert(role["role"], provider, "proxy_required", message),
        }
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        message = f"{config['label']}返回格式无效: {exc.__class__.__name__}"
        return {
            "provider": provider,
            "label": config["label"],
            "kind": config["kind"],
            "configured": True,
            "ok": False,
            "status": "invalid_response",
            "reason": "invalid_response",
            "message": message,
            "model": model,
            "alert": alert(role["role"], provider, "invalid_response", message),
        }
    except Exception as exc:
        message = f"{config['label']}连通性测试失败: {exc.__class__.__name__}"
        return {
            "provider": provider,
            "label": config["label"],
            "kind": config["kind"],
            "configured": True,
            "ok": False,
            "status": "provider_error",
            "reason": "provider_error",
            "message": message,
            "model": model,
            "alert": alert(role["role"], provider, "provider_error", message),
        }


@router.post("/providers/{provider}/test")
def provider_test(provider: str, request: ProviderTestRequest) -> dict[str, Any]:
    if provider not in PROVIDER_DEFINITIONS:
        return {
            "provider": provider,
            "ok": False,
            "status": "unknown_provider",
            "reason": "unknown_provider",
            "message": "未知 provider。",
        }
    role_name = request.role if request.role in ROLE_DEFINITIONS else "planner"
    role = role_config(role_name)
    definition = PROVIDER_DEFINITIONS[provider]
    if definition["kind"] == "account":
        runner_request = PlannerRequest(requirement=request.prompt, provider=provider)
        raw_plan, attempt = call_account_provider(role, provider, runner_request, [])
        return {
            "provider": provider,
            "label": provider_config(provider)["label"],
            "kind": "account",
            "configured": provider_config(provider)["configured"],
            "ok": raw_plan is not None,
            "status": attempt["reason"],
            "reason": attempt["reason"],
            "message": "Codex CLI 账号调用测试通过。" if raw_plan is not None else attempt.get("alert", {}).get("message", ""),
            "alert": attempt.get("alert"),
        }
    return test_api_provider(provider, role, request.prompt)


def normalize_tasks(raw_tasks: Any, request: PlannerRequest, stage: str) -> list[dict[str, Any]]:
    if not isinstance(raw_tasks, list):
        return []
    normalized_tasks: list[dict[str, Any]] = []
    for index, item in enumerate(raw_tasks[:12]):
        if not isinstance(item, dict):
            continue
        task_type = str(item.get("type") or "").strip()
        if task_type not in ALLOWED_PLANNER_TASK_TYPES:
            continue
        title = str(item.get("title") or f"Planner task {index + 1}: {task_type}")[:96]
        task_input = item.get("input") if isinstance(item.get("input"), dict) else {}
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        normalized_tasks.append(
            {
                "type": task_type,
                "title": title,
                "priority": int(item.get("priority", request.priority - index)),
                "input": task_input,
                "metadata": {"stage": stage, **metadata},
            }
        )
    return normalized_tasks


def normalize_plan(raw_plan: dict[str, Any], request: PlannerRequest) -> dict[str, Any]:
    normalized_tasks = normalize_tasks(raw_plan.get("tasks"), request, "llm_planner")
    if not normalized_tasks:
        return fallback_plan(request, "planner produced no allowed tasks")
    return {
        "source": raw_plan.get("source", "llm"),
        "model": raw_plan.get("model"),
        "summary": str(raw_plan.get("summary") or request.requirement),
        "risk_level": str(raw_plan.get("risk_level") or "medium"),
        "tasks": normalized_tasks,
    }


def run_role(role_name: str, request: PlannerRequest, prior_outputs: list[dict[str, Any]]) -> dict[str, Any]:
    role = role_config(role_name)
    attempts: list[dict[str, Any]] = []
    for provider in role["provider_order"]:
        allowed, gate = before_attempt(provider)
        if not allowed:
            attempts.append(
                {
                    "role": role_name,
                    "provider": provider,
                    "status": "skipped",
                    "reason": "cooldown",
                    "provider_health": gate,
                    "alert": alert(
                        role_name,
                        provider,
                        "fallback_active",
                        f"{provider} 暂时不可用，将在 {gate.get('retry_at') or '稍后'} 重新探测。",
                        "info",
                    ),
                }
            )
            continue
        definition = PROVIDER_DEFINITIONS.get(provider, {"kind": "api"})
        if definition["kind"] == "account":
            raw_plan, attempt = call_account_provider(role, provider, request, prior_outputs)
        else:
            raw_plan, attempt = call_api_provider(provider, role, request, prior_outputs)
        attempts.append(attempt)
        if raw_plan is not None:
            health = record_success(provider)
            attempt["provider_health"] = health
            if health["event"] == "recovered_to_preferred":
                attempt["alert"] = alert(
                    role_name,
                    provider,
                    "provider_recovered",
                    f"{provider} 已恢复，当前请求已回切到该优先 Provider。",
                    "info",
                )
            tasks = normalize_tasks(raw_plan.get("tasks"), request, f"role_{role_name}")
            return {
                "role": role_name,
                "label": role["label"],
                "model": raw_plan.get("model") or role["model"],
                "source": provider,
                "status": "succeeded",
                "summary": str(raw_plan.get("summary") or request.requirement),
                "risk_level": str(raw_plan.get("risk_level") or "medium"),
                "tasks": tasks,
                "notes": raw_plan.get("notes") if isinstance(raw_plan.get("notes"), list) else [],
                "attempts": attempts,
            }
        attempt["provider_health"] = record_failure(provider, str(attempt.get("reason") or "provider_error"))

    reason = "; ".join(f"{item['provider']}:{item['reason']}" for item in attempts if item.get("reason")) or "no provider"
    return {
        "role": role_name,
        "label": role["label"],
        "model": role["model"],
        "source": "fallback",
        "status": "fallback",
        "summary": f"{role['label']}未获得可自动调用模型输出，已进入 fallback。原因: {reason}",
        "risk_level": "medium",
        "tasks": fallback_tasks(request, reason) if role_name == "planner" else [],
        "notes": [f"{role['label']}需要人工或后续账号 provider 接入。"],
        "attempts": attempts,
    }


def collect_alerts(role_outputs: list[dict[str, Any]]) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for output in role_outputs:
        for attempt in output.get("attempts", []):
            item = attempt.get("alert")
            if not isinstance(item, dict):
                continue
            key = (str(item.get("role")), str(item.get("provider")), str(item.get("code")))
            if key in seen:
                continue
            seen.add(key)
            alerts.append({key: str(value) for key, value in item.items()})
    return alerts


def merge_approval_plan(role_outputs: list[dict[str, Any]], request: PlannerRequest) -> list[dict[str, Any]]:
    for role_name in ["planner", "supervisor", "coder", "reviewer", "risk"]:
        for output in role_outputs:
            if output["role"] == role_name and output.get("tasks"):
                tasks = normalize_tasks(output["tasks"], request, f"merged_{role_name}")
                if tasks:
                    return tasks[:12]
    return fallback_tasks(request, "no role produced allowed tasks")


def build_role_plan(request: PlannerRequest) -> dict[str, Any]:
    role_outputs: list[dict[str, Any]] = []
    for role_name in ROLE_EXECUTION_ORDER:
        role_outputs.append(run_role(role_name, request, role_outputs))
    approval_plan = merge_approval_plan(role_outputs, request)
    alerts = collect_alerts(role_outputs)
    planner_output = next((item for item in role_outputs if item["role"] == "planner"), role_outputs[0])
    highest_risk = "high" if any(item.get("risk_level") == "high" for item in role_outputs) else planner_output.get("risk_level", "medium")
    supervisor_output = next((item for item in role_outputs if item["role"] == "supervisor"), role_outputs[-1])
    supervisor_output["decision"] = {
        "verdict": "escalate" if highest_risk == "high" or supervisor_output.get("status") != "succeeded" else "human_approval_required",
        "automatic_execution_allowed": False,
        "reason": "高风险或监督模型不可用" if highest_risk == "high" or supervisor_output.get("status") != "succeeded" else "五角色证据完整，等待人工批准",
    }
    return {
        "source": "multi_role",
        "summary": planner_output.get("summary") or request.requirement,
        "risk_level": highest_risk,
        "tasks": approval_plan,
        "role_outputs": role_outputs,
        "alerts": alerts,
        "execution_order": ROLE_EXECUTION_ORDER,
        "roles": roles()["roles"],
    }


def build_plan(request: PlannerRequest) -> dict[str, Any]:
    if not request.provider:
        role_plan = build_role_plan(request)
        return {
            "source": role_plan["source"],
            "summary": role_plan["summary"],
            "risk_level": role_plan["risk_level"],
            "tasks": role_plan["tasks"],
            "role_outputs": role_plan["role_outputs"],
            "alerts": role_plan["alerts"],
            "execution_order": role_plan["execution_order"],
        }

    providers_to_try = [request.provider]
    errors: list[str] = []
    for provider in providers_to_try:
        try:
            role = role_config("planner")
            raw_plan, attempt = call_api_provider(provider, role, request, [])
            if raw_plan is None:
                errors.append(f"{provider}: {attempt.get('reason')}")
                continue
            plan = normalize_plan(raw_plan, request)
            plan["provider_errors"] = errors
            plan["alerts"] = collect_alerts([{"attempts": [attempt]}])
            return plan
        except Exception as exc:
            errors.append(f"{provider}: {exc.__class__.__name__}")
    plan = fallback_plan(request, "; ".join(errors) or "no configured provider")
    plan["provider_errors"] = errors
    return plan


@router.post("/plan")
def plan(request: PlannerRequest) -> dict[str, Any]:
    return build_plan(request)


@router.post("/multi-plan")
def multi_plan(request: PlannerRequest) -> dict[str, Any]:
    return build_role_plan(request)


def create_planner_tasks(request: PlannerRequest, plan: dict[str, Any], actor: str) -> dict[str, Any]:
    title = request.title or request.requirement[:40] or "新的项目需求"
    evidence = create_workflow_evidence(request.project, request.requirement, plan)
    common_metadata = {
        "source": "llm_planner",
        "planner_source": plan.get("source"),
        "planner_model": plan.get("model"),
        "project_repo": "192.168.31.17:/home/gryps/.openclaw/workspace/douyin-listing-workbench",
        "git_snapshot": capture_project_snapshot(),
        "workflow_id": evidence["workflow_id"],
        "evidence_root_hash": evidence["evidence_root_hash"],
        **request.metadata,
    }
    if plan.get("alerts"):
        common_metadata["planner_alert_codes"] = [item.get("code") for item in plan["alerts"][:8]]
    task_specs = [
        TaskCreate(
            project=request.project,
            type="ops.note",
            title=title_with("记录 LLM 需求", title),
            priority=request.priority + 10,
            input={"note": request.requirement, "planner_summary": plan.get("summary"), "alerts": plan.get("alerts", [])},
            metadata={**common_metadata, "stage": "llm_requirement_received"},
        ),
        TaskCreate(
            project=request.project,
            type="project.context.sync",
            title=title_with("LLM 前同步上下文", title),
            priority=request.priority + 5,
            input={"files": ["AGENTS.md", "PROJECT_STATE.md", "CONTEXT_PACK.md", "README.md"], "reason": request.requirement},
            metadata={**common_metadata, "stage": "llm_context_sync"},
        ),
        TaskCreate(
            project=request.project,
            type="project.git.status",
            title=title_with("LLM 前检查工作副本", title),
            priority=request.priority + 3,
            input={"reason": request.requirement},
            metadata={**common_metadata, "stage": "llm_workcopy_status"},
        ),
        TaskCreate(
            project=request.project,
            type="review.human",
            title=title_with("人工确认 LLM 计划", title),
            priority=request.priority,
            input={
                "requirement": request.requirement,
                "planner_summary": plan.get("summary"),
                "risk_level": plan.get("risk_level"),
                "instruction": "确认多角色 LLM planner 生成的任务计划；注意 provider fallback 和额度/鉴权提醒，批准前可编辑 approval_plan。",
                "approval_plan": plan["tasks"],
                "role_outputs": plan.get("role_outputs", []),
                "provider_attempts": [
                    attempt
                    for output in plan.get("role_outputs", [])
                    for attempt in output.get("attempts", [])
                ],
                "alerts": plan.get("alerts", []),
                "evidence_manifest": evidence,
                "supervisor_decision": next(
                    (output.get("decision") for output in plan.get("role_outputs", []) if output.get("role") == "supervisor"),
                    None,
                ),
            },
            metadata={**common_metadata, "stage": "llm_human_gate"},
        ),
    ]
    if request.idempotency_key:
        for index, spec in enumerate(task_specs):
            spec.idempotency_key = f"planner:{request.idempotency_key}:{index}"
    for spec in task_specs:
        spec.pipeline_id = request.pipeline_id
    tasks = create_task_graph(
        task_specs,
        {1: [0], 2: [1], 3: [2]},
        actor=actor,
        reason="planner created task graph",
    )
    return {
        "planner": plan,
        "project": request.project,
        "created": len(tasks),
        "workflow": evidence,
        "tasks": [
            {"id": task["id"], "type": task["type"], "title": task["title"], "state": task["state"], "priority": task["priority"]}
            for task in tasks
        ],
        "next": "在 review.human 中审核多角色计划、fallback/额度提醒；批准后才创建下游执行任务。",
    }


@router.post("/invoke")
def invoke(request: PlannerRequest) -> dict[str, Any]:
    return create_planner_tasks(request, build_plan(request), actor="llm_planner")


@router.post("/multi-invoke")
def multi_invoke(request: PlannerRequest) -> dict[str, Any]:
    return create_planner_tasks(request, build_role_plan(request), actor="multi_role_planner")
