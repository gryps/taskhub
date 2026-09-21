#!/bin/sh
set -eu

backup=${1:-}
[ -n "$backup" ] || { printf '用法: %s <备份目录>\n' "$0" >&2; exit 1; }
[ -d "$backup" ] || { printf '备份目录不存在: %s\n' "$backup" >&2; exit 1; }
backup=$(CDPATH= cd -- "$backup" && pwd)
for name in .env backup.env postgres.dump taskhub-data.tar.gz images.tar compose.yaml SHA256SUMS; do
  [ -f "$backup/$name" ] || { printf '备份缺少文件: %s\n' "$name" >&2; exit 1; }
done
(cd "$backup" && sha256sum -c SHA256SUMS)

backup_key=$(sed -n 's/^TASKHUB_CONFIG_ENCRYPTION_KEY=//p' "$backup/.env" | tail -n 1)
expected_fingerprint=$(sed -n 's/^TASKHUB_CONFIG_KEY_FINGERPRINT=//p' "$backup/backup.env" | tail -n 1)
actual_fingerprint=$(printf 'taskhub-backup-v1:%s' "$backup_key" | sha256sum | awk '{print $1}')
[ -n "$backup_key" ] && [ "$actual_fingerprint" = "$expected_fingerprint" ] || {
  printf '备份中的配置加密主密钥与备份身份不匹配。\n' >&2; exit 1;
}

postgres_image=$(sed -n 's/^TASKHUB_POSTGRES_IMAGE=//p' "$backup/backup.env" | tail -n 1)
postgres_image=${postgres_image:-postgres:16-alpine}
if ! docker image inspect "$postgres_image" >/dev/null 2>&1; then
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
