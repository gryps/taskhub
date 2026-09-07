# Ubuntu Node Template

`192.168.31.50` is the powered golden template for TaskHub Ubuntu hosts. It is
not a production node and must never be registered in `nodes.json`, hold model
credentials, start a TaskHub service, or accept a workload.

## Baseline

- Ubuntu 24.04 x86_64
- Python 3.12.3 in `/home/gryps/apps/taskhub-node/.venv`
- Node.js 22.22.1 and npm 10.9.4
- Git 2.43.0
- ripgrep 14.1.0, jq 1.7, Zip 3.0, and UnZip 6.00
- PostgreSQL client 16.15
- Corepack 0.34.6
- Codex CLI 0.153.2 without account authentication
- User namespaces available: `unshare -Ur true` succeeds for user `gryps`
- Codex workspace-write sandbox can edit a temporary Git worktree
- Exact Python packages from `config/ubuntu-template.lock`
- TaskHub wheel built from the commit recorded in
  `config/ubuntu-environment-baseline.json`

Run the template qualification from the repository root with the template venv:

```bash
/home/gryps/apps/taskhub-node/.venv/bin/python \
  scripts/check_ubuntu_environment.py --mode template
```

The command must return `status: qualified` before the VM is sealed.

## Clone Admission

1. Clone the powered-off template.
2. Before connecting the clone to the LAN, assign a unique MAC address, hostname,
   machine ID, and static IP.
3. Keep the OS, system tools, venv path, and locked Python packages unchanged.
4. Run `scripts/check_ubuntu_environment.py --mode node`.
5. Open TaskHub **系统配置** and confirm the node has no failed preflight
   checks before enabling coding workloads.
6. Configure that node's identity, token, credentials, service, and workload only
   after qualification succeeds.
7. Register and enable it in TaskHub last.

Ubuntu nodes with a different OS release, interpreter, Node.js version, package
set, or dependency fingerprint are rejected. A project venv must never be reused
as a TaskHub node venv. Windows browser nodes use their separate Windows baseline.
