#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
env_file="$root/.env"
[ -f "$env_file" ] || { printf '缺少 %s。\n' "$env_file" >&2; exit 1; }
env_value() { sed -n "s/^$1=//p" "$env_file" | tail -n 1; }

for name in taskhub-controller-1 taskhub-postgres-1 taskhub-docker-proxy-1; do
  state=$(docker inspect --format '{{.State.Status}}' "$name" 2>/dev/null || true)
  [ "$state" = running ] || { printf '容器未运行: %s (%s)\n' "$name" "${state:-missing}" >&2; exit 1; }
  printf '[通过] %s 正在运行\n' "$name"
done

controller_health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' taskhub-controller-1)
[ "$controller_health" = healthy ] || { printf 'Seed 健康状态异常: %s\n' "$controller_health" >&2; exit 1; }

port=$(env_value TASKHUB_PORT); port=${port:-8200}
scheme=http; curl_flags=-fsS
if [ "$(env_value TASKHUB_ENFORCE_HTTPS)" = true ]; then scheme=https; curl_flags=-fkSs; fi
health=$(curl $curl_flags "$scheme://127.0.0.1:$port/api/health")
printf '%s' "$health" | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"' || { printf 'API 健康响应异常。\n' >&2; exit 1; }
page=$(curl $curl_flags "$scheme://127.0.0.1:$port/")
printf '%s' "$page" | grep -q '/static/app.js' || { printf '前端入口缺少 app.js。\n' >&2; exit 1; }
printf '[通过] Web/API: %s://127.0.0.1:%s\n' "$scheme" "$port"

node_image=$(env_value TASKHUB_NODE_IMAGE)
expected_revision=$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$node_image")
nodes=$(docker ps -a --filter label=io.taskhub.managed=true --format '{{.Names}}')
if [ -z "$nodes" ]; then
  printf '[警告] 尚未创建工作节点；请在 Web 中创建后重新运行 verify.sh。\n'
else
  for name in $nodes; do
    state=$(docker inspect --format '{{.State.Status}}' "$name")
    health_state=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$name")
    configured_image=$(docker inspect --format '{{.Config.Image}}' "$name")
    node_revision=$(docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$name")
    role=$(docker inspect --format '{{index .Config.Labels "io.taskhub.role"}}' "$name")
    [ "$state:$health_state" = running:healthy ] || { printf '节点异常: %s %s/%s\n' "$name" "$state" "$health_state" >&2; exit 1; }
    if [ -n "$expected_revision" ]; then
      [ "$node_revision" = "$expected_revision" ] || { printf '节点镜像不是当前配置版本: %s\n' "$name" >&2; exit 1; }
    else
      [ "$configured_image" = "$node_image" ] || { printf '节点镜像引用不是当前配置版本: %s\n' "$name" >&2; exit 1; }
    fi
    coding=$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$name" | sed -n 's/^TASKHUB_NODE_CODING_ENABLED=//p')
    if [ "$role" = execution ]; then
      [ "$coding" = true ] || { printf '执行节点未启用编码能力: %s\n' "$name" >&2; exit 1; }
    else
      [ "$coding" = false ] || { printf '非执行节点错误启用编码能力: %s\n' "$name" >&2; exit 1; }
    fi
    printf '[通过] 节点 %s role=%s coding=%s\n' "$name" "$role" "$coding"
  done
fi
printf 'TaskHub 部署验收通过。\n'
