#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$root/checksum.sh"
env_file="$root/.env"
compose_file="$root/compose.yaml"
timestamp=$(date -u '+%Y%m%dT%H%M%SZ')
backup=${1:-"$root/backups/$timestamp"}

[ -f "$env_file" ] || { printf '缺少 %s。\n' "$env_file" >&2; exit 1; }
[ ! -e "$backup" ] || { printf '备份目录已存在: %s\n' "$backup" >&2; exit 1; }
mkdir -p "$backup"
chmod 700 "$backup"
cp "$env_file" "$backup/.env"
cp "$compose_file" "$backup/compose.yaml"

compose="docker compose --project-directory $root --env-file $env_file -f $compose_file"
postgres_image=$(sed -n 's/^TASKHUB_POSTGRES_IMAGE=//p' "$env_file" | tail -n 1)
seed_image=$(sed -n 's/^TASKHUB_SEED_IMAGE=//p' "$env_file" | tail -n 1)
node_image=$(sed -n 's/^TASKHUB_NODE_IMAGE=//p' "$env_file" | tail -n 1)
data_volume=$(sed -n 's/^TASKHUB_DATA_VOLUME=//p' "$env_file" | tail -n 1)
postgres_volume=$(sed -n 's/^TASKHUB_POSTGRES_VOLUME=//p' "$env_file" | tail -n 1)
reuse_image_backup=${TASKHUB_REUSE_IMAGE_BACKUP:-}
encryption_key=$(sed -n 's/^TASKHUB_CONFIG_ENCRYPTION_KEY=//p' "$env_file" | tail -n 1)
[ -n "$encryption_key" ] || { printf '配置加密主密钥为空，拒绝生成不可验证备份。\n' >&2; exit 1; }
key_fingerprint=$(printf 'taskhub-backup-v1:%s' "$encryption_key" | sha256_digest | awk '{print $1}')
case "$data_volume:$postgres_volume" in
  taskhub-data:taskhub-postgres-data|taskhub-seed_taskhub-data:taskhub-seed_postgres-data) ;;
  *) printf '数据卷名称不在 TaskHub 安全范围内。\n' >&2; exit 1 ;;
esac
if [ -n "$reuse_image_backup" ]; then
  "$root/verify-backup.sh" "$reuse_image_backup"
  prior_seed=$(sed -n 's/^TASKHUB_SEED_IMAGE=//p' "$reuse_image_backup/backup.env" | tail -n 1)
  prior_node=$(sed -n 's/^TASKHUB_NODE_IMAGE=//p' "$reuse_image_backup/backup.env" | tail -n 1)
  prior_postgres=$(sed -n 's/^TASKHUB_POSTGRES_IMAGE=//p' "$reuse_image_backup/backup.env" | tail -n 1)
  [ "$prior_seed" = "$seed_image" ] && [ "$prior_node" = "$node_image" ] \
    && [ "$prior_postgres" = "$postgres_image" ] || {
      printf '复用备份的运行镜像与当前部署不一致。\n' >&2
      exit 1
    }
  cp "$reuse_image_backup/images.tar" "$backup/images.tar"
else
  docker save -o "$backup/images.tar" "$seed_image" "$node_image" "$postgres_image"
fi

$compose stop controller
trap '$compose start controller >/dev/null 2>&1 || true' EXIT INT TERM
$compose exec -T postgres pg_dump -U taskhub -d taskhub -Fc >"$backup/postgres.dump"
docker run --rm -v "${data_volume:-taskhub-data}:/data" -v "$backup:/backup" "${postgres_image:-postgres:16-alpine}" \
  sh -c 'tar -czf /backup/taskhub-data.tar.gz -C /data .'
{
  printf 'TASKHUB_BACKUP_CREATED=%s\n' "$timestamp"
  printf 'TASKHUB_SEED_IMAGE=%s\n' "$seed_image"
  printf 'TASKHUB_NODE_IMAGE=%s\n' "$node_image"
  printf 'TASKHUB_POSTGRES_IMAGE=%s\n' "$postgres_image"
  printf 'TASKHUB_CONFIG_KEY_FINGERPRINT=%s\n' "$key_fingerprint"
} >"$backup/backup.env"
(cd "$backup" && sha256_digest .env backup.env postgres.dump taskhub-data.tar.gz images.tar compose.yaml >SHA256SUMS)
"$root/verify-backup.sh" "$backup"

$compose start controller
trap - EXIT INT TERM
printf '备份完成: %s\n' "$backup"
