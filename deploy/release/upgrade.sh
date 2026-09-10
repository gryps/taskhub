#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
version=${1:-}
bundle=${2:-}
[ -n "$version" ] || { printf '用法: %s <版本> [离线包目录]\n' "$0" >&2; exit 1; }
[ -f "$root/.env" ] || { printf '请在已初始化的 TaskHub 安装目录运行。\n' >&2; exit 1; }

if [ -n "$bundle" ]; then
  [ -f "$bundle/SHA256SUMS" ] || { printf '离线包缺少 SHA256SUMS。\n' >&2; exit 1; }
  (cd "$bundle" && sha256sum -c SHA256SUMS)
  manifest_version=$(sed -n 's/.*"version": "\([^"]*\)".*/\1/p' "$bundle/manifest.json" | head -n 1)
  [ "$manifest_version" = "$version" ] || {
    printf '离线包版本 %s 与目标版本 %s 不一致。\n' "$manifest_version" "$version" >&2
    exit 1
  }
  archive=$(find "$bundle/images" -maxdepth 1 -type f -name 'taskhub-images-*.tar' | head -n 1)
  docker load -i "$archive"
else
  registry=${TASKHUB_REGISTRY:-}
  prefix=""
  [ -z "$registry" ] || prefix="${registry%/}/"
  docker pull "${prefix}taskhub-seed:$version"
  docker pull "${prefix}taskhub-node:$version"
fi

backup=$("$root/backup.sh" | sed -n 's/^备份完成: //p')
[ -n "$backup" ] || { printf '升级前备份失败。\n' >&2; exit 1; }
if [ -n "$bundle" ]; then
  cp "$bundle/compose.yaml" "$root/compose.yaml"
fi

prefix=""
if [ -z "$bundle" ]; then
  registry=${TASKHUB_REGISTRY:-}
  [ -z "$registry" ] || prefix="${registry%/}/"
fi
sed -i.bak \
  -e "s|^TASKHUB_VERSION=.*|TASKHUB_VERSION=$version|" \
  -e "s|^TASKHUB_SEED_IMAGE=.*|TASKHUB_SEED_IMAGE=${prefix}taskhub-seed:$version|" \
  -e "s|^TASKHUB_NODE_IMAGE=.*|TASKHUB_NODE_IMAGE=${prefix}taskhub-node:$version|" \
  "$root/.env"
rm -f "$root/.env.bak"

if ! docker compose --project-directory "$root" --env-file "$root/.env" -f "$root/compose.yaml" \
  up -d --no-build --pull never; then
  printf '升级启动失败，开始恢复 %s。\n' "$backup" >&2
  "$root/restore.sh" "$backup"
  exit 1
fi

port=$(sed -n 's/^TASKHUB_PORT=//p' "$root/.env" | tail -n 1)
attempts=0
while [ "$attempts" -lt 60 ]; do
  if curl -fsS "http://127.0.0.1:${port:-8200}/api/health" >/dev/null 2>&1; then
    printf '升级完成: %s；恢复点: %s\n' "$version" "$backup"
    exit 0
  fi
  attempts=$((attempts + 1))
  sleep 3
done
printf '升级后健康检查失败，开始恢复 %s。\n' "$backup" >&2
"$root/restore.sh" "$backup"
exit 1
