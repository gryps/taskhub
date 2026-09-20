#!/bin/sh
set -eu

install_docker=false
case "${1:-}" in
  "") ;;
  --install-docker) install_docker=true ;;
  *) printf '用法: %s [--install-docker]\n' "$0" >&2; exit 2 ;;
esac

[ -r /etc/os-release ] || { printf '无法识别操作系统。\n' >&2; exit 1; }
. /etc/os-release
[ "${ID:-}" = ubuntu ] || { printf '仅支持 Ubuntu，当前是 %s。\n' "${ID:-unknown}" >&2; exit 1; }
case "${VERSION_ID:-}" in
  22.04|24.04) ;;
  *) printf '仅验收 Ubuntu 22.04/24.04，当前是 %s。\n' "${VERSION_ID:-unknown}" >&2; exit 1 ;;
esac

architecture=$(dpkg --print-architecture 2>/dev/null || uname -m)
case "$architecture" in
  amd64|x86_64) ;;
  *) printf '当前公开 TaskHub 镜像仅支持 amd64，主机是 %s。\n' "$architecture" >&2; exit 1 ;;
esac

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  printf 'Docker 与 Compose 已安装。\n'
else
  $install_docker || {
    printf 'Docker Engine/Compose 未就绪。确认变更窗口后运行: %s --install-docker\n' "$0" >&2
    exit 1
  }
  command -v sudo >/dev/null 2>&1 || { printf '安装 Docker 需要 sudo。\n' >&2; exit 1; }
  conflicts=$(dpkg-query -W -f='${binary:Package}\n' \
    docker.io docker-compose docker-compose-v2 docker-doc docker-buildx podman-docker \
    containerd runc 2>/dev/null || true)
  [ -z "$conflicts" ] || {
    printf '检测到可能冲突的软件包，请由管理员评估并移除后重试:\n%s\n' "$conflicts" >&2
    exit 1
  }
  sudo apt-get update
  sudo apt-get install -y ca-certificates curl openssl
  sudo install -m 0755 -d /etc/apt/keyrings
  sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  sudo chmod a+r /etc/apt/keyrings/docker.asc
  codename=${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}
  [ -n "$codename" ] || { printf '无法识别 Ubuntu 代号。\n' >&2; exit 1; }
  temporary=/tmp/taskhub-docker.sources.$$
  trap 'rm -f "$temporary"' EXIT INT TERM
  {
    printf 'Types: deb\n'
    printf 'URIs: https://download.docker.com/linux/ubuntu\n'
    printf 'Suites: %s\n' "$codename"
    printf 'Components: stable\n'
    printf 'Architectures: %s\n' "$(dpkg --print-architecture)"
    printf 'Signed-By: /etc/apt/keyrings/docker.asc\n'
  } >"$temporary"
  sudo install -m 0644 "$temporary" /etc/apt/sources.list.d/docker.sources
  sudo apt-get update
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  sudo systemctl enable --now docker
  sudo usermod -aG docker "$(id -un)"
  printf 'Docker 已安装。请重新登录 Ubuntu 会话，再运行 ./preflight.sh。\n'
  exit 0
fi

docker info >/dev/null 2>&1 || {
  printf 'Docker 已安装但当前用户无法连接。请确认 Docker 已启动且用户属于 docker 组，然后重新登录。\n' >&2
  exit 1
}
command -v curl >/dev/null 2>&1 || { printf '缺少 curl，请安装 curl。\n' >&2; exit 1; }
command -v openssl >/dev/null 2>&1 || { printf '缺少 openssl，请安装 openssl。\n' >&2; exit 1; }
printf 'Ubuntu 宿主机准备完成。下一步: ./preflight.sh && ./init.sh\n'
