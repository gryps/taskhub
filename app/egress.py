from __future__ import annotations

import os
from typing import Any

import httpx


OPENAI_PROVIDERS = {"gpt_api", "openai", "chatgpt_plus_account", "chatgpt_pro_account"}


class ProxyRequiredError(RuntimeError):
    pass


def openai_proxy_url() -> str:
    return (
        os.getenv("OPENAI_PROXY_URL", "").strip()
        or os.getenv("HTTPS_PROXY", "").strip()
        or os.getenv("HTTP_PROXY", "").strip()
        or os.getenv("ALL_PROXY", "").strip()
    )


def openai_proxy_required() -> bool:
    return os.getenv("OPENAI_PROXY_REQUIRED", "true").lower() in {"1", "true", "yes", "on"}


def egress_policy(provider: str) -> dict[str, Any]:
    if provider in OPENAI_PROVIDERS:
        proxy_url = openai_proxy_url()
        if openai_proxy_required() and not proxy_url:
            raise ProxyRequiredError("OpenAI network proxy is required")
        return {"route": "proxy", "proxy": proxy_url or None, "trust_env": False}
    return {"route": "direct", "proxy": None, "trust_env": False}


def provider_http_client(provider: str, timeout: float) -> httpx.Client:
    policy = egress_policy(provider)
    return httpx.Client(
        proxy=policy["proxy"],
        trust_env=False,
        timeout=timeout,
    )


def apply_openai_proxy_environment(env: dict[str, str]) -> dict[str, str]:
    policy = egress_policy("chatgpt_plus_account")
    result = dict(env)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        result.pop(key, None)
    proxy_url = policy["proxy"]
    if proxy_url:
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            result[key] = proxy_url
    return result
