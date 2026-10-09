#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$root/checksum.sh"

backup=${1:-}
[ -n "$backup" ] || { printf '用法: %s <备份目录>\n' "$0" >&2; exit 1; }
[ -d "$backup" ] || { printf '备份目录不存在: %s\n' "$backup" >&2; exit 1; }
backup=$(CDPATH= cd -- "$backup" && pwd)
for name in .env backup.env postgres.dump taskhub-data.tar.gz compose.yaml SHA256SUMS; do
  [ -f "$backup/$name" ] || { printf '备份缺少文件: %s\n' "$name" >&2; exit 1; }
done
image_mode=$(sed -n 's/^TASKHUB_BACKUP_IMAGE_MODE=//p' "$backup/backup.env" | tail -n 1)
image_mode=${image_mode:-archive}
case "$image_mode" in archive)
  [ -f "$backup/images.tar" ] || { printf '备份缺少文件: images.tar\n' >&2; exit 1; } ;;
  reference) ;;
  *) printf '备份镜像模式无效。\n' >&2; exit 1 ;;
esac
(cd "$backup" && sha256_verify SHA256SUMS)

backup_key=$(sed -n 's/^TASKHUB_CONFIG_ENCRYPTION_KEY=//p' "$backup/.env" | tail -n 1)
expected_fingerprint=$(sed -n 's/^TASKHUB_CONFIG_KEY_FINGERPRINT=//p' "$backup/backup.env" | tail -n 1)
actual_fingerprint=$(printf 'taskhub-backup-v1:%s' "$backup_key" | sha256_digest | awk '{print $1}')
[ -n "$backup_key" ] && [ "$actual_fingerprint" = "$expected_fingerprint" ] || {
  printf '备份中的配置加密主密钥与备份身份不匹配。\n' >&2; exit 1;
}

postgres_image=$(sed -n 's/^TASKHUB_POSTGRES_IMAGE=//p' "$backup/backup.env" | tail -n 1)
postgres_image=${postgres_image:-postgres:16-alpine}
if [ "$image_mode" = reference ]; then
  for prefix in SEED NODE POSTGRES; do
    image=$(sed -n "s/^TASKHUB_${prefix}_IMAGE=//p" "$backup/backup.env" | tail -n 1)
    expected=$(sed -n "s/^TASKHUB_${prefix}_IMAGE_ID=//p" "$backup/backup.env" | tail -n 1)
    actual=$(docker image inspect "$image" --format '{{.Id}}' 2>/dev/null || true)
    [ -n "$expected" ] && [ "$actual" = "$expected" ] || {
      printf '本机回退镜像缺失或摘要不匹配: %s\n' "$image" >&2; exit 1;
    }
  done
elif ! docker image inspect "$postgres_image" >/dev/null 2>&1; then
  docker load -i "$backup/images.tar" >/dev/null
fi
docker run --rm -v "$backup:/backup:ro" "$postgres_image" \
  pg_restore -l /backup/postgres.dump | grep -q taskhub_backup_identity || {
    printf 'PostgreSQL 备份缺少加密密钥身份表。\n' >&2; exit 1;
  }
identity_data=$(docker run --rm -v "$backup:/backup:ro" "$postgres_image" \
  pg_restore -a -t taskhub_backup_identity -f - /backup/postgres.dump)
printf '%s\n' "$identity_data" | grep -q "$expected_fingerprint" || {
  printf 'PostgreSQL 备份身份与配置加密主密钥不匹配。\n' >&2; exit 1;
}
docker run --rm -v "$backup:/backup:ro" "$postgres_image" \
  tar -tzf /backup/taskhub-data.tar.gz >/dev/null
printf '备份验证通过: %s\n' "$backup"
