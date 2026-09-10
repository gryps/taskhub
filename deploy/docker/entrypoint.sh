#!/bin/sh
set -eu

data_root="${TASKHUB_DATA_ROOT:-/var/lib/taskhub}"
config_root="${data_root}/config"

mkdir -p \
  "${config_root}" \
  "${data_root}/state" \
  "${data_root}/repositories" \
  "${data_root}/workspaces" \
  "${data_root}/artifacts"

if [ ! -e "${TASKHUB_PROJECTS_FILE:-${config_root}/projects.json}" ]; then
  printf '{"projects":[]}\n' > "${TASKHUB_PROJECTS_FILE:-${config_root}/projects.json}"
fi

if [ ! -e "${TASKHUB_NODES_FILE:-${config_root}/nodes.json}" ]; then
  printf '{"nodes":[]}\n' > "${TASKHUB_NODES_FILE:-${config_root}/nodes.json}"
fi

providers_file="${TASKHUB_PROVIDER_SECRETS_FILE:-${config_root}/providers.env}"
if [ ! -e "${providers_file}" ]; then
  : > "${providers_file}"
  chmod 0600 "${providers_file}"
fi

exec "$@"
