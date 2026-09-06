import asyncio
import json
import os
from contextlib import suppress
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from taskhub_v2.providers.egress import account_environment


def unavailable(kind: str, detail: str) -> dict[str, Any]:
    return {"kind": kind, "status": "unavailable", "detail": detail, "metrics": []}


class ModelUsageReader:
    def __init__(self, codex_bin: str, proxy_url: str, timeout: int = 12):
        self.codex_bin = codex_bin
        self.proxy_url = proxy_url
        self.timeout = timeout

    async def account(self, home: str, plan: str) -> dict[str, Any]:
        try:
            payload = await self._account_snapshot(home)
            snapshot = self._codex_snapshot(payload)
            windows = [snapshot.get("primary"), snapshot.get("secondary")]
            metrics = [self._window(item) for item in windows if item]
            metrics = [item for item in metrics if item]
            if plan == "pro":
                weekly = [item for item in metrics if item["window_minutes"] >= 7 * 24 * 60]
                metrics = weekly or metrics[-1:]
            return {
                "kind": "account_limits",
                "status": "available" if metrics else "unavailable",
                "plan": snapshot.get("planType") or plan,
                "metrics": metrics,
                "detail": "" if metrics else "账号未返回额度窗口",
            }
        except Exception as exc:
            return unavailable("account_limits", self._safe_error(exc))

    async def deepseek_balance(self, api_key: str, base_url: str) -> dict[str, Any]:
        if not api_key:
            return unavailable("api_balance", "API Key 未配置")
        url = base_url.rstrip("/")
        if url.endswith("/v1"):
            url = url[:-3]
        try:
            payload = await asyncio.to_thread(self._get_json, f"{url}/user/balance", api_key)
            metrics = [{"label": item.get("currency", "余额"),
                        "value": item.get("total_balance"), "unit": item.get("currency", "")}
                       for item in payload.get("balance_infos", [])]
            return {"kind": "api_balance", "status": "available", "metrics": metrics,
                    "detail": "" if payload.get("is_available", True) else "余额不足"}
        except Exception as exc:
            return unavailable("api_balance", self._safe_error(exc))

    async def minimax_balance(self, api_key: str) -> dict[str, Any]:
        if not api_key:
            return unavailable("api_balance", "API Key 未配置")
        try:
            payload = await asyncio.to_thread(
                self._get_json, "https://www.minimaxi.com/v1/token_plan/remains", api_key
            )
            metrics = self._minimax_metrics(payload)
            return {"kind": "api_balance", "status": "available" if metrics else "unavailable",
                    "metrics": metrics, "detail": "" if metrics else "接口未返回可识别的剩余额度"}
        except Exception as exc:
            return unavailable("api_balance", self._safe_error(exc))

    async def _account_snapshot(self, home: str) -> dict[str, Any]:
        process = await asyncio.create_subprocess_exec(
            self.codex_bin, "app-server", "--stdio",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=account_environment(dict(os.environ), self.proxy_url, home),
        )
        messages = [
            {"id": 1, "method": "initialize", "params": {
                "clientInfo": {"name": "taskhub-v2", "version": "2"},
                "capabilities": {"experimentalApi": True},
            }},
            {"method": "initialized", "params": {}},
            {"id": 2, "method": "account/rateLimits/read", "params": None},
        ]
        assert process.stdin and process.stdout
        process.stdin.write("".join(json.dumps(item) + "\n" for item in messages).encode())
        await process.stdin.drain()
        try:
            async with asyncio.timeout(self.timeout):
                while line := await process.stdout.readline():
                    response = json.loads(line)
                    if response.get("id") == 2:
                        if response.get("error"):
                            raise RuntimeError(response["error"].get("message", "额度查询失败"))
                        return response.get("result") or {}
            raise RuntimeError("额度查询未返回结果")
        finally:
            with suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 2)
            except TimeoutError:
                process.kill()
                await process.wait()

    def _get_json(self, url: str, api_key: str) -> dict[str, Any]:
        request = Request(url, headers={"Authorization": f"Bearer {api_key}",
                                        "Content-Type": "application/json"})
        opener = build_opener(ProxyHandler({}))
        with opener.open(request, timeout=self.timeout) as response:
            return json.load(response)

    @staticmethod
    def _codex_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
        buckets = payload.get("rateLimitsByLimitId") or {}
        return buckets.get("codex") or payload.get("rateLimits") or {}

    @staticmethod
    def _window(value: dict[str, Any]) -> dict[str, Any] | None:
        if "usedPercent" not in value:
            return None
        minutes = int(value.get("windowDurationMins") or 0)
        label = "周消耗" if minutes >= 7 * 24 * 60 else "5 小时消耗"
        return {"label": label, "used_percent": int(value["usedPercent"]),
                "window_minutes": minutes, "resets_at": value.get("resetsAt")}

    @classmethod
    def _remaining_metrics(cls, payload: Any, prefix: str = "") -> list[dict[str, Any]]:
        metrics = []
        if isinstance(payload, dict):
            for key, value in payload.items():
                path = f"{prefix}.{key}" if prefix else key
                lowered = key.lower()
                if isinstance(value, (str, int, float)) and any(
                    word in lowered for word in ("remain", "balance", "left", "credit")
                ):
                    metrics.append({"label": path, "value": value, "unit": ""})
                elif isinstance(value, (dict, list)):
                    metrics.extend(cls._remaining_metrics(value, path))
        elif isinstance(payload, list):
            for index, value in enumerate(payload):
                metrics.extend(cls._remaining_metrics(value, f"{prefix}[{index}]"))
        return metrics[:4]

    @staticmethod
    def _minimax_metrics(payload: dict[str, Any]) -> list[dict[str, Any]]:
        rows = payload.get("model_remains") or []
        if not rows:
            return ModelUsageReader._remaining_metrics(payload)
        row = rows[0]
        metrics = []
        for key, label in (("current_interval_remaining_percent", "5 小时剩余"),
                           ("current_weekly_remaining_percent", "周剩余")):
            if key in row:
                metrics.append({"label": label, "value": row[key], "unit": "%"})
        return metrics

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        if isinstance(exc, HTTPError):
            return f"余额接口返回 HTTP {exc.code}"
        if isinstance(exc, (URLError, TimeoutError, asyncio.TimeoutError)):
            return "余额接口暂时不可达"
        return str(exc)[:160] or "余额查询失败"
