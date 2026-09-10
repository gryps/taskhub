#!/bin/sh
set -eu

script_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_root/../.." && pwd)
version=${TASKHUB_VERSION:-0.1.0-alpha}
platform=${TASKHUB_PLATFORM:-linux/amd64}
arch=${platform#linux/}
output=${1:-"$repo_root/dist/taskhub-offline-${version}-${arch}"}

TASKHUB_REGISTRY= TASKHUB_PUSH=false TASKHUB_VERSION="$version" \
  TASKHUB_PLATFORM="$platform" "$script_root/build-images.sh"
docker image inspect postgres:16-alpine >/dev/null 2>&1 || docker pull --platform "$platform" postgres:16-alpine

if [ -e "$output" ]; then
  printf '输出目录已存在，请移动或删除后重试: %s\n' "$output" >&2
  exit 1
fi
mkdir -p "$output/images" "$output/docs"
cp "$script_root/README.md" "$script_root/compose.yaml" "$script_root/.env.example" "$output/"
cp "$script_root"/*.sh "$script_root"/*.ps1 "$output/"
cp "$repo_root/docs/deployment/ubuntu.md" "$repo_root/docs/deployment/windows-docker-desktop.md" "$output/docs/"

archive="images/taskhub-images-${version}-${arch}.tar"
docker save -o "$output/$archive" \
  "taskhub-seed:${version}" "taskhub-node:${version}" postgres:16-alpine

seed_id=$(docker image inspect --format '{{.Id}}' "taskhub-seed:${version}")
node_id=$(docker image inspect --format '{{.Id}}' "taskhub-node:${version}")
postgres_id=$(docker image inspect --format '{{.Id}}' postgres:16-alpine)
cat >"$output/manifest.json" <<EOF
{
  "product": "TaskHub",
  "version": "$version",
  "platform": "$platform",
  "images": {
    "taskhub-seed:${version}": "$seed_id",
    "taskhub-node:${version}": "$node_id",
    "postgres:16-alpine": "$postgres_id"
  }
}
EOF
(cd "$output" && find . -type f ! -name SHA256SUMS -print | sort | \
  sed 's|^\./||' | xargs sha256sum >SHA256SUMS)
printf '离线交付包已生成: %s\n' "$output"
