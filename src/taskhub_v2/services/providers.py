import asyncio
from pathlib import Path
from typing import Any

from taskhub_v2.config import Settings
from taskhub_v2.providers.codex_account import CodexAccountProvider
from taskhub_v2.providers.health import ProviderHealthStore
from taskhub_v2.security import mask_secret
from taskhub_v2.services.model_usage import ModelUsageReader, unavailable


DEVICE_AUTH_URL = "https://auth.openai.com/codex/device"


class ProviderCatalog:
    def __init__(self, settings: Settings, health: ProviderHealthStore | None = None):
        self.settings = settings
        self.health = health
        self.usage = ModelUsageReader(settings.codex_cli_bin, settings.openai_proxy_url)

    async def status(self) -> dict[str, Any]:
        plus, pro, plus_usage, pro_usage, deepseek_balance, minimax_balance = await asyncio.gather(
            self._account("chatgpt_plus_account", "plus", self.settings.codex_plus_home),
            self._account("chatgpt_pro_account", "pro", self.settings.codex_pro_home),
            self.usage.account(self.settings.codex_plus_home, "plus"),
            self.usage.account(self.settings.codex_pro_home, "pro"),
            self.usage.deepseek_balance(
                self.settings.deepseek_api_key, self.settings.deepseek_base_url
            ),
            self.usage.minimax_balance(self.settings.minimax_api_key),
        )
        plus["billing"] = plus_usage
        pro["billing"] = pro_usage
        providers = [
            plus,
            pro,
            self._api(
                "gpt_api",
                self.settings.gpt_api_key,
                self.settings.gpt_base_url,
                self.settings.gpt_model,
                "proxy",
            ),
            self._api(
                "deepseek_api",
                self.settings.deepseek_api_key,
                self.settings.deepseek_base_url,
                self.settings.deepseek_model,
                "direct",
            ),
            self._api(
                "minimax_api",
                self.settings.minimax_api_key,
                self.settings.minimax_base_url,
                self.settings.minimax_model,
                "direct",
            ),
        ]
        providers[2]["billing"] = unavailable(
            "api_balance", "普通项目 API Key 不提供账户剩余余额；需组织管理员账单权限"
        )
        providers[3]["billing"] = deepseek_balance
        providers[4]["billing"] = minimax_balance
        health = self.health.snapshot() if self.health else {}
        for provider in providers:
            record = health.get(provider["id"])
            if record:
                provider["runtime_health"] = record
                available, _ = self.health.availability(provider["id"])
                if not available:
                    provider["status"] = f"cooldown:{record['reason']}"
        account_roles = {
            "planner": "account_default",
            "coder": "account_default",
            "supervisor": "account_default",
        }
        plus["role_models"] = account_roles
        pro["role_models"] = account_roles
        providers[2]["role_models"] = {
            "planner": self.settings.gpt_planner_model,
            "coder": self.settings.gpt_coder_model,
            "supervisor": self.settings.gpt_supervisor_model,
        }
        providers[3]["role_models"] = {"reviewer": self.settings.deepseek_model}
        providers[4]["role_models"] = {"risk": self.settings.minimax_model}
        return {
            "active_mode": self.settings.provider,
            "device_auth_url": DEVICE_AUTH_URL,
            "proxy": {
                "required_for": ["chatgpt_plus_account", "chatgpt_pro_account", "gpt_api"],
                "configured": bool(self.settings.openai_proxy_url),
                "route": self.settings.openai_proxy_url,
                "direct": ["deepseek_api", "minimax_api"],
            },
            "providers": providers,
            "role_models": {
                "planner": self.settings.gpt_planner_model,
                "coder": self.settings.gpt_coder_model,
                "supervisor": self.settings.gpt_supervisor_model,
                "reviewer": self.settings.deepseek_model,
                "risk": self.settings.minimax_model,
            },
        }

    async def _account(self, provider_id: str, account: str, home: str) -> dict[str, Any]:
        provider = CodexAccountProvider(
            provider_id=provider_id,
            codex_bin=self.settings.codex_cli_bin,
            codex_home=home,
            proxy_url=self.settings.openai_proxy_url,
            workdir=self.settings.provider_workdir,
        )
        state = await provider.status()
        auth_file = Path(home) / "auth.json"
        return {
            "id": provider_id,
            "kind": "account",
            "configured": state["authenticated"],
            "status": state["status"],
            "model": "account_default",
            "route": "proxy",
            "credential_ref": str(auth_file),
            "credential_present": auth_file.is_file(),
            "login_command": (
                "ssh -p 8022 -t gryps@192.168.31.31 "
                f"'/home/gryps/apps/taskhub-v2/scripts/codex_account_login.sh {account}'"
            ),
            "device_auth_url": DEVICE_AUTH_URL,
        }

    @staticmethod
    def _api(
        provider_id: str, api_key: str, base_url: str, model: str, route: str
    ) -> dict[str, Any]:
        return {
            "id": provider_id,
            "kind": "api",
            "configured": bool(api_key),
            "status": "configured" if api_key else "not_configured",
            "api_key_mask": mask_secret(api_key),
            "base_url": base_url,
            "model": model,
            "route": route,
        }
