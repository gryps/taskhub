import pytest

from taskhub_v2.config import Settings
from taskhub_v2.providers.codex_account import CodexAccountProvider
from taskhub_v2.services.providers import ProviderCatalog


@pytest.mark.anyio
async def test_provider_catalog_uses_effective_model_cards(monkeypatch, tmp_path):
    settings = Settings(
        model_account_root=str(tmp_path / "accounts"),
        openai_proxy_url="http://global-proxy:7890",
        model_cards=[
            {
                "model_id": "configured-pro",
                "display_name": "ChatGPT Pro Card",
                "service_type": "openai",
                "auth_mode": "account",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-5.6-sol",
                "proxy_url": "http://card-proxy:7893",
                "enabled": True,
                "assignments": [{"role": "planner", "priority": 0}],
            },
            {
                "model_id": "configured-minimax",
                "display_name": "MiniMax Card",
                "service_type": "minimax",
                "auth_mode": "api",
                "base_url": "https://api.minimax.cn/v1",
                "model": "MiniMax-M2.7",
                "proxy_url": "",
                "enabled": True,
                "assignments": [{"role": "reviewer", "priority": 0}],
                "api_key": "test-minimax-key",
            },
        ],
    )
    observed = {}

    async def account_status(_provider):
        return {"authenticated": True, "status": "authenticated"}

    async def account_usage(home, plan, proxy_url=None):
        observed["account"] = (home, plan, proxy_url)
        return {"kind": "account_limits", "status": "available", "metrics": []}

    async def minimax_usage(api_key, proxy_url=""):
        observed["minimax"] = (api_key, proxy_url)
        return {"kind": "api_balance", "status": "available", "metrics": []}

    monkeypatch.setattr(CodexAccountProvider, "status", account_status)
    catalog = ProviderCatalog(settings)
    monkeypatch.setattr(catalog.usage, "account", account_usage)
    monkeypatch.setattr(catalog.usage, "minimax_balance", minimax_usage)

    result = await catalog.status()
    providers = {item["id"]: item for item in result["providers"]}

    assert set(providers) == {"configured-pro", "configured-minimax"}
    assert providers["configured-pro"]["display_name"] == "ChatGPT Pro Card"
    assert providers["configured-pro"]["configured"] is True
    assert providers["configured-pro"]["proxy_configured"] is True
    assert providers["configured-minimax"]["configured"] is True
    assert providers["configured-minimax"]["api_key_mask"] == "test********-key"
    assert observed["account"][1:] == ("pro", "http://card-proxy:7893")
    assert observed["minimax"] == ("test-minimax-key", "")
    assert result["role_models"] == {
        "planner": "gpt-5.6-sol",
        "reviewer": "MiniMax-M2.7",
    }
