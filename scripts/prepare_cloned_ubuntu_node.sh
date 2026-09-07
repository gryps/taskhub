#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "run as root" >&2
  exit 1
fi

swap_size=${TASKHUB_SWAP_SIZE:-2G}

# VM clones must not retain the template's system or SSH server identity.
truncate -s 0 /etc/machine-id
systemd-machine-id-setup

printf 'y\n' | ssh-keygen -q -t rsa -b 3072 -N '' -f /etc/ssh/ssh_host_rsa_key
printf 'y\n' | ssh-keygen -q -t ecdsa -b 521 -N '' -f /etc/ssh/ssh_host_ecdsa_key
printf 'y\n' | ssh-keygen -q -t ed25519 -N '' -f /etc/ssh/ssh_host_ed25519_key

if ! swapon --show=NAME --noheadings | grep -qx /swapfile; then
  if [[ ! -f /swapfile ]]; then
    fallocate -l "${swap_size}" /swapfile
  fi
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
fi
if ! grep -q '^/swapfile ' /etc/fstab; then
  printf '/swapfile none swap sw 0 0\n' | tee -a /etc/fstab >/dev/null
fi

systemctl restart ssh
printf 'machine_id=%s swap=%s\n' "$(cat /etc/machine-id)" "$(swapon --show=SIZE --noheadings)"
