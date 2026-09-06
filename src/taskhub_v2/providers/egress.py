import os


class ProxyRequiredError(RuntimeError):
    pass


OPENAI_CHANNELS = {"gpt_api", "chatgpt_plus_account", "chatgpt_pro_account"}
PROXY_VARIABLES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


def provider_proxy(provider: str, proxy_url: str) -> str | None:
    if provider not in OPENAI_CHANNELS:
        return None
    if not proxy_url.strip():
        raise ProxyRequiredError(f"OpenAI provider {provider} requires a proxy")
    return proxy_url.strip()


def account_environment(base: dict[str, str], proxy_url: str, codex_home: str) -> dict[str, str]:
    result = direct_environment(base)
    proxy = provider_proxy("chatgpt_plus_account", proxy_url) or ""
    for key in PROXY_VARIABLES:
        result[key] = proxy
    result["CODEX_HOME"] = codex_home
    for key in (
        "OPENAI_API_KEY",
        "OPENAI_ACCESS_TOKEN",
        "CODEX_ACCESS_TOKEN",
        "TASKHUB_GPT_API_KEY",
    ):
        result.pop(key, None)
    return result


def direct_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    result = dict(base or os.environ)
    for key in PROXY_VARIABLES:
        result.pop(key, None)
    return result
