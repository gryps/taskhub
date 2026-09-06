#!/usr/bin/env bash
set -euo pipefail

secrets_file="${TASKHUB_PROVIDER_SECRETS_FILE:-/home/gryps/.config/taskhub-v2/providers.env}"
proxy_url="${TASKHUB_OPENAI_PROXY_URL:-}"
if [[ -z "$proxy_url" && -r "$secrets_file" ]]; then
  proxy_url="$(sed -n 's/^TASKHUB_OPENAI_PROXY_URL=//p' "$secrets_file" | head -n 1)"
  proxy_url="${proxy_url%\"}"
  proxy_url="${proxy_url#\"}"
fi
if [[ -z "$proxy_url" ]]; then
  echo "OpenAI proxy is required but not configured" >&2
  return 78
fi
export HTTP_PROXY="$proxy_url" HTTPS_PROXY="$proxy_url" ALL_PROXY="$proxy_url"
export http_proxy="$proxy_url" https_proxy="$proxy_url" all_proxy="$proxy_url"
