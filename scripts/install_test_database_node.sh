#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "run as root" >&2
  exit 1
fi

node_user=${TASKHUB_NODE_USER:-gryps}
database=${TASKHUB_TEST_DATABASE_NAME:-postgres}
role=${TASKHUB_TEST_DATABASE_ROLE:-taskhub_test_admin}
env_names=${TASKHUB_TEST_DATABASE_ENV_VARS:-TASKHUB_TEST_POSTGRES_DSN}
password=$(openssl rand -hex 24)
home=$(getent passwd "${node_user}" | cut -d: -f6)
env_file="${home}/.config/taskhub-node/providers.env"

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y postgresql postgresql-client
systemctl enable --now postgresql
version=$(pg_lsclusters --no-header | awk 'NR==1 {print $1}')
port=$(pg_lsclusters --no-header | awk 'NR==1 {print $3}')
sudo -u postgres psql -v ON_ERROR_STOP=1 --set=role="${role}" --set=password="${password}" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN CREATEDB PASSWORD %L', :'role', :'password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'role') \gexec
SELECT format('ALTER ROLE %I LOGIN CREATEDB PASSWORD %L', :'role', :'password') \gexec
SQL
install -d -m 700 -o "${node_user}" -g "${node_user}" "$(dirname "${env_file}")"
touch "${env_file}"
sed -i '/^TASKHUB_TEST_DATABASE_ADMIN_DSN=/d;/^TASKHUB_TEST_DATABASE_ENV_VARS=/d' "${env_file}"
printf 'TASKHUB_TEST_DATABASE_ADMIN_DSN=postgresql://%s:%s@127.0.0.1:%s/%s\n' \
  "${role}" "${password}" "${port}" "${database}" >> "${env_file}"
printf 'TASKHUB_TEST_DATABASE_ENV_VARS=%s\n' "${env_names}" >> "${env_file}"
chown "${node_user}:${node_user}" "${env_file}"
chmod 600 "${env_file}"
systemctl --user -M "${node_user}@" daemon-reload
systemctl --user -M "${node_user}@" restart taskhub-node.service
printf 'PostgreSQL %s test database capability installed on localhost:%s for %s\n' \
  "${version}" "${port}" "${node_user}"
