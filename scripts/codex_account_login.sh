#!/usr/bin/env bash
set -euo pipefail

account="${1:-}"
case "$account" in
  plus) codex_home="${TASKHUB_CODEX_PLUS_HOME:-/home/gryps/.codex-plus}" ;;
  pro) codex_home="${TASKHUB_CODEX_PRO_HOME:-/home/gryps/.codex-pro}" ;;
  *) echo "usage: $0 plus|pro" >&2; exit 2 ;;
esac
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$script_dir/codex_proxy_env.sh"
mkdir -p "$codex_home"
chmod 700 "$codex_home"
unset OPENAI_API_KEY OPENAI_ACCESS_TOKEN CODEX_ACCESS_TOKEN TASKHUB_GPT_API_KEY
export CODEX_HOME="$codex_home"

echo "Codex account: $account"
echo "Official device authorization page: https://auth.openai.com/codex/device"
echo "Enter the device code shown below on that page."
exec "${TASKHUB_CODEX_CLI_BIN:-/home/gryps/.local/bin/codex}" login --device-auth
