#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
backup=${1:-}
[ -n "$backup" ] || { printf '用法: %s <备份目录>\n' "$0" >&2; exit 1; }
[ -d "$backup" ] || { printf '备份目录不存在: %s\n' "$backup" >&2; exit 1; }
(cd "$backup" && sha256sum -c SHA256SUMS)

docker compose --project-directory "$root" --env-file "$backup/.env" -f "$backup/compose.yaml" down
docker load -i "$backup/images.tar"
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
$compose up -d --no-build --pull never
printf '已从备份恢复: %s\n' "$backup"
