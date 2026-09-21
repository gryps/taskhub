#!/usr/bin/env python3
import argparse
from pathlib import Path

from taskhub_v2.security.secrets import parse_env_file, write_env_file

MAPPING = {
    "LLM_GPT_API_KEY": "TASKHUB_GPT_API_KEY",
    "LLM_GPT_BASE_URL": "TASKHUB_GPT_BASE_URL",
    "LLM_GPT_MODEL": "TASKHUB_GPT_MODEL",
    "LLM_DEEPSEEK_API_KEY": "TASKHUB_DEEPSEEK_API_KEY",
    "LLM_DEEPSEEK_BASE_URL": "TASKHUB_DEEPSEEK_BASE_URL",
    "LLM_DEEPSEEK_MODEL": "TASKHUB_DEEPSEEK_MODEL",
    "LLM_MINIMAX_API_KEY": "TASKHUB_MINIMAX_API_KEY",
    "LLM_MINIMAX_BASE_URL": "TASKHUB_MINIMAX_BASE_URL",
    "LLM_MINIMAX_MODEL": "TASKHUB_MINIMAX_MODEL",
    "OPENAI_PROXY_URL": "TASKHUB_OPENAI_PROXY_URL",
    "CODEX_PLUS_HOME": "TASKHUB_CODEX_PLUS_HOME",
    "CODEX_PRO_HOME": "TASKHUB_CODEX_PRO_HOME",
    "CODEX_CLI_BIN": "TASKHUB_CODEX_CLI_BIN",
    "LANGGRAPH_ADMIN_TOKEN": "TASKHUB_ADMIN_TOKEN",
    "AUTH_SESSION_SECRET": "TASKHUB_SESSION_SECRET",
    "AUTH_COOKIE_SECURE": "TASKHUB_COOKIE_SECURE",
}


def migrate(source: Path, target: Path) -> list[str]:
    source_values = parse_env_file(source)
    target_values = parse_env_file(target)
    migrated = []
    for old_key, new_key in MAPPING.items():
        value = source_values.get(old_key, "").strip()
        if value:
            target_values[new_key] = value
            migrated.append(new_key)
    write_env_file(target, target_values)
    return migrated


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate provider settings without values")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    migrated = migrate(args.source, args.target)
    print(f"migrated {len(migrated)} provider settings")
    for key in migrated:
        print(f"- {key}")


if __name__ == "__main__":
    main()
