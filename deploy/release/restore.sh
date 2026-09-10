#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
backup=${1:-}
[ -n "$backup" ] || { printf '用法: %s <备份目录>\n' "$0" >&2; exit 1; }
[ -d "$backup" ] || { printf '备份目录不存在: %s\n' "$backup" >&2; exit 1; }
(cd "$backup" && sha256sum -c SHA256SUMS)

backup_key=$(sed -n 's/^TASKHUB_CONFIG_ENCRYPTION_KEY=//p' "$backup/.env" | tail -n 1)
expected_fingerprint=$(sed -n 's/^TASKHUB_CONFIG_KEY_FINGERPRINT=//p' "$backup/backup.env" | tail -n 1)
actual_fingerprint=$(printf 'taskhub-backup-v1:%s' "$backup_key" | sha256sum | awk '{print $1}')
[ -n "$backup_key" ] && [ "$actual_fingerprint" = "$expected_fingerprint" ] || {
  printf '备份中的配置加密主密钥与备份身份不匹配。\n' >&2; exit 1;
}
backup_postgres_image=$(sed -n 's/^TASKHUB_POSTGRES_IMAGE=//p' "$backup/backup.env" | tail -n 1)
docker load -i "$backup/images.tar"
docker run --rm -v "$backup:/backup:ro" "${backup_postgres_image:-postgres:16-alpine}" \
  pg_restore -l /backup/postgres.dump | grep -q taskhub_backup_identity || {
    printf 'PostgreSQL 备份缺少加密密钥身份表。\n' >&2; exit 1;
  }
identity_data=$(docker run --rm -v "$backup:/backup:ro" \
  "${backup_postgres_image:-postgres:16-alpine}" \
  pg_restore -a -t taskhub_backup_identity -f - /backup/postgres.dump)
printf '%s\n' "$identity_data" | grep -q "$expected_fingerprint" || {
  printf 'PostgreSQL 备份身份与配置加密主密钥不匹配。\n' >&2; exit 1;
}

docker compose --project-directory "$root" --env-file "$backup/.env" -f "$backup/compose.yaml" down
cp "$backup/.env" "$root/.env"
cp "$backup/compose.yaml" "$root/compose.yaml"
postgres_image=$(sed -n 's/^TASKHUB_POSTGRES_IMAGE=//p' "$root/.env" | tail -n 1)
data_volume=$(sed -n 's/^TASKHUB_DATA_VOLUME=//p' "$root/.env" | tail -n 1)
postgres_volume=$(sed -n 's/^TASKHUB_POSTGRES_VOLUME=//p' "$root/.env" | tail -n 1)
case "$data_volume:$postgres_volume" in
  taskhub-data:taskhub-postgres-data|taskhub-seed_taskhub-data:taskhub-seed_postgres-data) ;;
  *) printf '备份中的数据卷名称不在 TaskHub 安全范围内。\n' >&2; exit 1 ;;
esac

for volume in "$data_volume" "$postgres_volume"; do
  docker volume create "$volume" >/dev/null
  docker run --rm -v "$volume:/data" "${postgres_image:-postgres:16-alpine}" \
    sh -c 'find /data -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +'
done
docker run --rm -v "$data_volume:/data" -v "$backup:/backup:ro" \
  "${postgres_image:-postgres:16-alpine}" sh -c 'tar -xzf /backup/taskhub-data.tar.gz -C /data'

compose="docker compose --project-directory $root --env-file $root/.env -f $root/compose.yaml"
$compose up -d postgres
attempts=0
until $compose exec -T postgres pg_isready -U taskhub -d taskhub >/dev/null 2>&1; do
  attempts=$((attempts + 1))
  [ "$attempts" -lt 40 ] || { printf '恢复时 PostgreSQL 未就绪。\n' >&2; exit 1; }
  sleep 2
done
$compose exec -T postgres pg_restore -U taskhub -d taskhub --clean --if-exists <"$backup/postgres.dump"
database_fingerprint=$($compose exec -T postgres psql -U taskhub -d taskhub -Atc \
  'SELECT key_fingerprint FROM taskhub_backup_identity WHERE identity_id=1')
[ "$database_fingerprint" = "$expected_fingerprint" ] || {
  printf '恢复后的数据库与配置加密主密钥不匹配，控制器未启动。\n' >&2; exit 1;
}
$compose up -d --no-build --pull never
printf '已从备份恢复: %s\n' "$backup"
