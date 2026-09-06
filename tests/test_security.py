from pathlib import Path

from taskhub_v2.providers.egress import (
    ProxyRequiredError,
    account_environment,
    direct_environment,
    provider_proxy,
)
from taskhub_v2.security.secrets import mask_secret, parse_env_file, write_env_file


def test_openai_requires_proxy_and_non_openai_is_direct():
    try:
        provider_proxy("gpt_api", "")
        raise AssertionError("missing proxy should fail")
    except ProxyRequiredError:
        pass
    assert provider_proxy("gpt_api", "http://proxy:7893") == "http://proxy:7893"
    assert provider_proxy("deepseek_api", "http://proxy:7893") is None


def test_account_environment_removes_api_key_and_sets_proxy():
    result = account_environment(
        {"OPENAI_API_KEY": "secret", "HTTPS_PROXY": "old"},
        "http://proxy:7893",
        "/secure/account",
    )
    assert "OPENAI_API_KEY" not in result
    assert result["HTTPS_PROXY"] == "http://proxy:7893"
    assert result["CODEX_HOME"] == "/secure/account"


def test_direct_environment_removes_inherited_proxy():
    result = direct_environment({"HTTPS_PROXY": "proxy", "KEEP": "yes"})
    assert "HTTPS_PROXY" not in result
    assert result["KEEP"] == "yes"


def test_secret_file_is_round_trippable_and_private(tmp_path: Path):
    path = tmp_path / "providers.env"
    write_env_file(path, {"KEY": "abc def", "TOKEN": "secret"})
    assert parse_env_file(path) == {"KEY": "abc def", "TOKEN": "secret"}
    assert path.stat().st_mode & 0o777 == 0o600
    assert mask_secret("abcdefghijkl") == "abcd********ijkl"
