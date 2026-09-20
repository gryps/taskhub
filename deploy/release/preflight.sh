#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
env_file=${1:-"$root/.env"}
failures=0
warnings=0

pass() { printf '[通过] %s\n' "$1"; }
warn() { printf '[警告] %s\n' "$1"; warnings=$((warnings + 1)); }
fail() { printf '[失败] %s\n' "$1" >&2; failures=$((failures + 1)); }
env_value() { sed -n "s/^$1=//p" "$env_file" 2>/dev/null | tail -n 1; }
remote_image_exists() {
  if command -v timeout >/dev/null 2>&1; then timeout 30 docker manifest inspect "$1" >/dev/null 2>&1
  else docker manifest inspect "$1" >/dev/null 2>&1
  fi
}

for command_name in docker curl openssl; do
  command -v "$command_name" >/dev/null 2>&1 && pass "命令可用: $command_name" || fail "缺少命令: $command_name"
done
if command -v docker >/dev/null 2>&1; then
  docker info >/dev/null 2>&1 && pass 'Docker Engine 可访问' || fail 'Docker Engine 未启动或当前用户无权限'
  docker compose version >/dev/null 2>&1 && pass 'Docker Compose v2 可用' || fail '缺少 Docker Compose v2'
  server_arch=$(docker version --format '{{.Server.Arch}}' 2>/dev/null || true)
  case "$server_arch" in amd64|x86_64) pass 'Docker 架构为 amd64' ;; *) fail "当前公开镜像不支持 Docker 架构: ${server_arch:-unknown}" ;; esac
fi

cpu_count=$(getconf _NPROCESSORS_ONLN 2>/dev/null || printf 0)
[ "$cpu_count" -ge 4 ] && pass "CPU: $cpu_count 核" || warn "CPU 仅 $cpu_count 核，建议至少 4 核"
memory_kb=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null || printf 0)
[ "$memory_kb" -ge 7864320 ] && pass '内存不少于 8 GB' || warn '内存低于建议的 8 GB'
available_kb=$(df -Pk "$root" | awk 'NR==2 {print $4}')
[ "${available_kb:-0}" -ge 41943040 ] && pass '可用磁盘不少于 40 GB' || warn '可用磁盘低于建议的 40 GB'

[ -f "$env_file" ] || {
  warn "尚未创建 $env_file；将按 .env.example/default 初始化"
  env_file="$root/.env.example"
}
[ -f "$env_file" ] || fail '缺少 .env 和 .env.example'

port=$(env_value TASKHUB_PORT); port=${port:-8200}
if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "(^|:)$port$"; then
  if docker ps --format '{{.Names}}' | grep -qx taskhub-controller-1; then pass "端口 $port 已由现有 TaskHub 使用"; else fail "端口 $port 已被其他进程占用"; fi
else
  pass "端口 $port 未发现冲突"
fi

for key in TASKHUB_SEED_IMAGE TASKHUB_NODE_IMAGE TASKHUB_POSTGRES_IMAGE TASKHUB_DOCKER_PROXY_IMAGE; do
  image=$(env_value "$key")
  if [ -z "$image" ]; then
    case "$key" in
      TASKHUB_SEED_IMAGE) image=ghcr.io/gryps/taskhub-seed:0.1.0-alpha ;;
      TASKHUB_NODE_IMAGE) image=ghcr.io/gryps/taskhub-node:0.1.0-alpha ;;
      TASKHUB_POSTGRES_IMAGE) image=postgres:16-alpine ;;
      TASKHUB_DOCKER_PROXY_IMAGE) image=ghcr.io/tecnativa/docker-socket-proxy:v0.5.0 ;;
    esac
    warn "$key 未显式配置，将使用 Compose 默认值 $image"
  fi
  if docker image inspect "$image" >/dev/null 2>&1 || remote_image_exists "$image"; then
    pass "镜像可用: $image"
  else
    fail "无法读取镜像: $image"
  fi
done

[ "$failures" -eq 0 ] || { printf '预检失败: %s 项失败，%s 项警告。\n' "$failures" "$warnings" >&2; exit 1; }
printf '预检通过: %s 项警告。可以运行 ./init.sh。\n' "$warnings"
