#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
env_file="$root/.env"
compose_file="$root/compose.yaml"

require() {
  command -v "$1" >/dev/null 2>&1 || {
    printf '缺少命令: %s\n' "$1" >&2
    exit 1
  }
}

random_hex() {
  openssl rand -hex "$1"
}

fernet_key() {
  openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n'
}

env_value() {
  sed -n "s/^$1=//p" "$env_file" | tail -n 1
}

put_env_value() {
  key=$1
  value=$2
  if grep -q "^${key}=" "$env_file"; then
    temporary="${env_file}.tmp.$$"
    awk -v key="$key" -v value="$value" '
      index($0, key "=") == 1 { print key "=" value; next }
      { print }
    ' "$env_file" >"$temporary"
    mv "$temporary" "$env_file"
  else
    printf '%s=%s\n' "$key" "$value" >>"$env_file"
  fi
}

ensure_env_value() {
  key=$1
  default=$2
  [ -n "$(env_value "$key")" ] || put_env_value "$key" "$default"
}

ensure_secret() {
  key=$1
  value=$(env_value "$key")
  case "$value" in
    ''|replace-with-*) put_env_value "$key" "$2" ;;
  esac
}

verify_image_platforms() {
  server_arch=$(docker version --format '{{.Server.Arch}}')
  case "$server_arch" in
    x86_64) server_arch=amd64 ;;
    aarch64) server_arch=arm64 ;;
  esac
  expected="linux/$server_arch"
  for image in \
    "$(env_value TASKHUB_SEED_IMAGE)" \
    "$(env_value TASKHUB_NODE_IMAGE)" \
    "$(env_value TASKHUB_POSTGRES_IMAGE)"; do
    actual=$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image")
    [ "$actual" = "$expected" ] || {
      printf '镜像架构不匹配: %s 是 %s，Docker 主机是 %s。\n' "$image" "$actual" "$expected" >&2
      exit 1
    }
  done
}

wait_for_health() {
  port=$(env_value TASKHUB_PORT)
  attempts=0
  while [ "$attempts" -lt 60 ]; do
    if curl -fsS "http://127.0.0.1:${port:-8200}/api/health" >/dev/null 2>&1; then
      printf 'TaskHub Seed 已启动: http://127.0.0.1:%s\n' "${port:-8200}"
      printf '首次设置口令保存在 %s 的 TASKHUB_ADMIN_TOKEN。\n' "$env_file"
      return
    fi
    attempts=$((attempts + 1))
    sleep 3
  done
  docker compose --project-directory "$root" --env-file "$env_file" -f "$compose_file" ps
  docker compose --project-directory "$root" --env-file "$env_file" -f "$compose_file" logs --tail 80 controller
  printf 'TaskHub Seed 健康检查超时。\n' >&2
  exit 1
}

require docker
require openssl
require curl
docker info >/dev/null 2>&1 || {
  printf 'Docker 未启动，或当前用户无权访问 Docker。请先修复用户权限；脚本不会请求 sudo 密码。\n' >&2
  exit 1
}
docker compose version >/dev/null 2>&1 || {
  printf '需要 Docker Compose v2（docker compose）。\n' >&2
  exit 1
}

docker_gid=0
if [ -S /var/run/docker.sock ] && command -v stat >/dev/null 2>&1; then
  docker_gid=$(stat -c '%g' /var/run/docker.sock 2>/dev/null || printf '0')
fi
data_volume=taskhub-data
postgres_volume=taskhub-postgres-data
if ! docker volume inspect taskhub-data >/dev/null 2>&1 &&
  ! docker volume inspect taskhub-postgres-data >/dev/null 2>&1 &&
  docker volume inspect taskhub-seed_taskhub-data >/dev/null 2>&1 &&
  docker volume inspect taskhub-seed_postgres-data >/dev/null 2>&1; then
  data_volume=taskhub-seed_taskhub-data
  postgres_volume=taskhub-seed_postgres-data
fi

if [ ! -f "$env_file" ]; then
  umask 077
  : >"$env_file"
  printf '已生成仅本机可读的 %s。\n' "$env_file"
fi
chmod 600 "$env_file"
ensure_env_value TASKHUB_VERSION 0.1.0-alpha
ensure_env_value TASKHUB_PORT 8200
ensure_env_value TASKHUB_SEED_IMAGE taskhub-seed:0.1.0-alpha
ensure_env_value TASKHUB_NODE_IMAGE taskhub-node:0.1.0-alpha
ensure_env_value TASKHUB_POSTGRES_IMAGE postgres:16-alpine
ensure_env_value TASKHUB_DATA_VOLUME "$data_volume"
ensure_env_value TASKHUB_POSTGRES_VOLUME "$postgres_volume"
ensure_env_value TASKHUB_DOCKER_GID "$docker_gid"
ensure_env_value TASKHUB_COOKIE_SECURE false
ensure_env_value TASKHUB_OPENAI_PROXY_URL ""
ensure_secret TASKHUB_POSTGRES_PASSWORD "$(random_hex 24)"
ensure_secret TASKHUB_ADMIN_TOKEN "$(random_hex 24)"
ensure_secret TASKHUB_SESSION_SECRET "$(random_hex 48)"
ensure_secret TASKHUB_CONFIG_ENCRYPTION_KEY "$(fernet_key)"

archive=$(find "$root/images" -maxdepth 1 -type f -name 'taskhub-images-*.tar' 2>/dev/null | head -n 1 || true)
if [ -n "$archive" ]; then
  require sha256sum
  (cd "$root" && sha256sum -c SHA256SUMS)
  bundle_platform=$(sed -n 's/.*"platform": "\([^"]*\)".*/\1/p' "$root/manifest.json" | head -n 1)
  server_arch=$(docker version --format '{{.Server.Arch}}')
  case "$server_arch" in
    x86_64) server_arch=amd64 ;;
    aarch64) server_arch=arm64 ;;
  esac
  [ "$bundle_platform" = "linux/$server_arch" ] || {
    printf '离线包平台 %s 与 Docker 主机 linux/%s 不匹配。\n' "$bundle_platform" "$server_arch" >&2
    exit 1
  }
  docker load -i "$archive"
else
  for image in \
    "$(env_value TASKHUB_SEED_IMAGE)" \
    "$(env_value TASKHUB_NODE_IMAGE)" \
    "$(env_value TASKHUB_POSTGRES_IMAGE)"; do
    if ! docker image inspect "$image" >/dev/null 2>&1; then
      docker pull "$image"
    fi
  done
fi
verify_image_platforms

docker compose --project-directory "$root" --env-file "$env_file" -f "$compose_file" config >/dev/null
docker compose --project-directory "$root" --env-file "$env_file" -f "$compose_file" up -d --no-build --pull never
wait_for_health
