#!/bin/sh
set -eu

script_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_root/../.." && pwd)
version=${TASKHUB_VERSION:-0.1.0-alpha}
output=${1:-"$repo_root/dist/taskhub-release-$version"}
archive=${2:-"$output.tar.gz"}
[ ! -e "$output" ] || { printf '输出目录已存在: %s\n' "$output" >&2; exit 1; }
[ ! -e "$archive" ] || { printf '输出文件已存在: %s\n' "$archive" >&2; exit 1; }
mkdir -p "$output/docs"
for file in "$script_root"/*.sh "$script_root"/*.ps1 "$script_root/compose.yaml" "$script_root/.env.example" "$script_root/README.md"; do
  cp "$file" "$output/"
done
cp "$repo_root/docs/deployment/ubuntu.md" "$repo_root/docs/deployment/windows-docker-desktop.md" "$output/docs/"
if command -v sha256sum >/dev/null 2>&1; then checksum='sha256sum'
elif command -v shasum >/dev/null 2>&1; then checksum='shasum -a 256'
else printf '缺少 sha256sum 或 shasum。\n' >&2; exit 1
fi
(cd "$output" && find . -type f ! -name SHA256SUMS -print | sort | sed 's|^./||' | xargs $checksum >SHA256SUMS)
tar -czf "$archive" -C "$(dirname "$output")" "$(basename "$output")"
printf '在线发布包: %s\n' "$archive"
