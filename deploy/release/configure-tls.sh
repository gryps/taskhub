#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
tls_dir="$root/tls"
hostname=""
ip_address=""
certificate=""
private_key=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --hostname) hostname=${2:-}; shift 2 ;;
    --ip) ip_address=${2:-}; shift 2 ;;
    --cert) certificate=${2:-}; shift 2 ;;
    --key) private_key=${2:-}; shift 2 ;;
    *) printf '用法: %s (--hostname NAME [--ip ADDRESS] | --cert FILE --key FILE)\n' "$0" >&2; exit 2 ;;
  esac
done
command -v openssl >/dev/null 2>&1 || { printf '缺少 openssl。\n' >&2; exit 1; }
mkdir -p "$tls_dir"
umask 077
if [ -n "$certificate" ] || [ -n "$private_key" ]; then
  [ -f "$certificate" ] && [ -f "$private_key" ] || { printf '--cert 与 --key 必须同时指向有效文件。\n' >&2; exit 1; }
  openssl x509 -in "$certificate" -noout >/dev/null
  openssl pkey -in "$private_key" -noout >/dev/null
  cert_pub=$(openssl x509 -in "$certificate" -pubkey -noout | openssl sha256)
  key_pub=$(openssl pkey -in "$private_key" -pubout | openssl sha256)
  [ "$cert_pub" = "$key_pub" ] || { printf '证书与私钥不匹配。\n' >&2; exit 1; }
  cp "$certificate" "$tls_dir/taskhub.crt"
  cp "$private_key" "$tls_dir/taskhub.key"
else
  [ -n "$hostname" ] || { printf '必须提供 --hostname，或同时提供 --cert/--key。\n' >&2; exit 2; }
  printf '%s' "$hostname" | grep -Eq '^[A-Za-z0-9.-]+$' || { printf '主机名格式无效。\n' >&2; exit 2; }
  san="DNS:$hostname,DNS:localhost,IP:127.0.0.1"
  if [ -n "$ip_address" ]; then
    printf '%s' "$ip_address" | grep -Eq '^[0-9A-Fa-f:.]+$' || { printf 'IP 地址格式无效。\n' >&2; exit 2; }
    san="$san,IP:$ip_address"
  fi
  openssl req -x509 -newkey rsa:3072 -nodes -days 397 \
    -keyout "$tls_dir/taskhub.key" -out "$tls_dir/taskhub.crt" \
    -subj "/CN=$hostname" -addext "subjectAltName=$san"
fi
chmod 600 "$tls_dir/taskhub.key"
printf 'TLS 文件已写入 %s。若 TaskHub 已运行，请执行: docker compose --env-file .env -f compose.yaml up -d --force-recreate controller\n' "$tls_dir"
