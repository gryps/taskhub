from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any

import httpx

from taskhub_v2.config import Settings
from taskhub_v2.providers.egress import ProxyRequiredError, account_environment
from taskhub_v2.services.configuration_views import safe_error

MINIMAX_TEXT_MODELS = [
    {"id": "MiniMax-M3", "name": "MiniMax M3"},
    {"id": "MiniMax-M2.7", "name": "MiniMax M2.7"},
    {"id": "MiniMax-M2.7-highspeed", "name": "MiniMax M2.7 Highspeed"},
]


def normalize_model_catalog(
    payload: Any, *, visible_only: bool = False
) -> list[dict[str, str]]:
    if not isinstance(payload, dict):
        raise ValueError("模型接口未返回 JSON 对象")
    items = payload.get("data", payload.get("models", []))
    if not isinstance(items, list):
        raise ValueError("模型接口未返回模型列表")
    models: dict[str, dict[str, str]] = {}
    for item in items[:1000]:
        if not isinstance(item, dict):
            continue
        if visible_only and item.get("visibility") not in {None, "list"}:
            continue
        model_id = str(item.get("id") or item.get("slug") or "").strip()
        if not model_id or len(model_id) > 200:
            continue
        models[model_id] = {
            "id": model_id,
            "name": str(item.get("display_name") or item.get("name") or model_id)[:200],
        }
    return sorted(models.values(), key=lambda item: item["id"].lower())[:500]


async def read_account_model_catalog(settings: Settings, card: dict[str, Any]) -> dict:
    account_home = Path(settings.model_account_root) / card["model_id"]
    if not (account_home / "auth.json").is_file():
        return {"available": False, "detail": "尚未完成账号授权", "models": []}
    binary = settings.codex_cli_bin
    if "/" not in binary:
        binary = shutil.which(binary) or ""
    if not binary or not Path(binary).is_file():
        return {"available": False, "detail": "Seed 控制器尚未安装 Codex CLI", "models": []}
    proxy_url = card.get("proxy_url") or settings.openai_proxy_url
    try:
        environment = account_environment(dict(os.environ), proxy_url, str(account_home))
        process = await asyncio.create_subprocess_exec(
            binary,
            "debug",
            "models",
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        if process.returncode:
            detail = stderr.decode("utf-8", errors="replace").strip()[-300:]
            return {
                "available": False,
                "detail": detail or "Codex CLI 无法读取账号模型列表",
                "models": [],
            }
        if len(stdout) > 2_000_000:
            raise ValueError("Codex CLI 返回的模型目录过大")
        models = normalize_model_catalog(
            json.loads(stdout.decode("utf-8")), visible_only=True
        )
        return {
            "available": True,
            "detail": f"ChatGPT 账号可用，读取到 {len(models)} 个模型",
            "models": models,
        }
    except TimeoutError:
        process.kill()
        await process.wait()
        return {"available": False, "detail": "读取账号模型列表超时", "models": []}
    except (OSError, ValueError, TypeError, ProxyRequiredError) as exc:
        return {"available": False, "detail": safe_error(exc), "models": []}


async def test_model_card(
    settings: Settings, card: dict[str, Any], secrets: dict[str, str]
) -> dict[str, Any]:
    if card["auth_mode"] == "account":
        return await read_account_model_catalog(settings, card)
    key = secrets.get(f"model_card__{card['model_id']}", "")
    if not key:
        return {"available": False, "detail": "尚未配置 API Key", "models": []}
    if any(character.isspace() for character in key):
        return {
            "available": False,
            "detail": "API Key 格式异常：不能包含空格或换行，请重新复制纯 Key",
            "models": [],
        }
    try:
        options: dict[str, Any] = {"timeout": 12, "trust_env": False}
        if card.get("proxy_url"):
            options["proxy"] = card["proxy_url"]
        if card.get("service_type") == "minimax":
            return await _test_minimax_card(card, key, options)
        async with httpx.AsyncClient(**options) as client:
            response = await client.get(
                f"{card['base_url'].rstrip('/')}/models",
                headers={"Authorization": f"Bearer {key}"},
            )
        models = normalize_model_catalog(response.json()) if response.is_success else []
        return {
            "available": response.status_code < 400,
            "detail": (
                f"连接成功，读取到 {len(models)} 个模型"
                if response.is_success
                else f"服务返回 HTTP {response.status_code}"
            ),
            "models": models,
        }
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        return {"available": False, "detail": safe_error(exc), "models": []}


async def _test_minimax_card(
    card: dict[str, Any], key: str, options: dict[str, Any]
) -> dict[str, Any]:
    china = ".cn" in card["base_url"] or "minimaxi.com" in card["base_url"]
    endpoint = (
        "https://www.minimaxi.com/v1/token_plan/remains"
        if china
        else "https://www.minimax.io/v1/token_plan/remains"
    )
    async with httpx.AsyncClient(**options) as client:
        response = await client.get(
            endpoint,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
    payload = response.json() if response.is_success else {}
    status = payload.get("base_resp", {}).get("status_code")
    if not response.is_success or status != 0:
        detail = f"MiniMax 拒绝 API Key（HTTP {response.status_code}"
        if status is not None:
            detail += f"，错误码 {status}"
        return {"available": False, "detail": f"{detail}）", "models": []}
    return {
        "available": True,
        "detail": f"MiniMax 认证成功，提供 {len(MINIMAX_TEXT_MODELS)} 个文本模型",
        "models": MINIMAX_TEXT_MODELS,
    }
