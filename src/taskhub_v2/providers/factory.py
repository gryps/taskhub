from taskhub_v2.config import Settings
from taskhub_v2.providers.base import ModelProvider
from taskhub_v2.providers.chat_compatible import ChatCompatibleProvider
from taskhub_v2.providers.codex_account import CodexAccountProvider
from taskhub_v2.providers.deterministic import DeterministicProvider
from taskhub_v2.providers.fallback import FallbackModelProvider
from taskhub_v2.providers.health import ProviderHealthStore
from taskhub_v2.providers.openai import OpenAIResponsesProvider


def build_provider(
    settings: Settings, health: ProviderHealthStore | None = None
) -> ModelProvider:
    if settings.provider == "deterministic":
        return DeterministicProvider()
    if settings.provider == "openai":
        return OpenAIResponsesProvider(
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            proxy_url=settings.openai_proxy_url,
            planner_model=settings.gpt_planner_model,
            supervisor_model=settings.gpt_supervisor_model,
        )
    providers = build_provider_registry(settings)
    return FallbackModelProvider(
        providers,
        routes={
            "planner": ["chatgpt_plus_account", "chatgpt_pro_account", "gpt_api"],
            "reviewer": ["deepseek_api"],
            "risk": ["minimax_api"],
            "supervisor": ["chatgpt_plus_account", "chatgpt_pro_account", "gpt_api"],
        },
        health=health,
    )


def build_provider_registry(settings: Settings) -> dict[str, ModelProvider]:
    account_args = {
        "codex_bin": settings.codex_cli_bin,
        "proxy_url": settings.openai_proxy_url,
        "workdir": settings.provider_workdir,
    }
    return {
        "chatgpt_plus_account": CodexAccountProvider(
            "chatgpt_plus_account", codex_home=settings.codex_plus_home, **account_args
        ),
        "chatgpt_pro_account": CodexAccountProvider(
            "chatgpt_pro_account", codex_home=settings.codex_pro_home, **account_args
        ),
        "gpt_api": OpenAIResponsesProvider(
            base_url=settings.gpt_base_url,
            api_key=settings.gpt_api_key,
            model=settings.gpt_model,
            proxy_url=settings.openai_proxy_url,
            planner_model=settings.gpt_planner_model,
            supervisor_model=settings.gpt_supervisor_model,
        ),
        "deepseek_api": ChatCompatibleProvider(
            "deepseek_api",
            settings.deepseek_base_url,
            settings.deepseek_api_key,
            settings.deepseek_model,
        ),
        "minimax_api": ChatCompatibleProvider(
            "minimax_api",
            settings.minimax_base_url,
            settings.minimax_api_key,
            settings.minimax_model,
        ),
    }
