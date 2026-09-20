# TaskHub Release Kit

This directory is the source for the standard TaskHub delivery bundle. It is
separate from `deploy/seed`, which remains the Windows rapid-development setup.

## Runtime images

- `taskhub-seed:<version>`: Web/API/LangGraph control plane.
- `taskhub-node:<version>`: one role-gated image for execution, test and preproduction nodes.
- `postgres:16-alpine`: private controller database and checkpoint store.
- `ghcr.io/tecnativa/docker-socket-proxy:v0.5.0`: internal-only restricted Docker API gateway.

TaskHub `0.1.0-alpha` is publicly available from either registry:

| Registry | Seed | Node |
| --- | --- | --- |
| GHCR | `ghcr.io/gryps/taskhub-seed:0.1.0-alpha` | `ghcr.io/gryps/taskhub-node:0.1.0-alpha` |
| Aliyun ACR | `crpi-kgqnka7pmz9f3sml.cn-hangzhou.personal.cr.aliyuncs.com/taskhub-v2/taskhub-seed:0.1.0-alpha` | `crpi-kgqnka7pmz9f3sml.cn-hangzhou.personal.cr.aliyuncs.com/taskhub-v2/taskhub-node:0.1.0-alpha` |

Both repositories allow anonymous pulls. Use the Aliyun ACR references when its
Hangzhou endpoint is faster from the deployment network.

Online initialization preloads Seed, Node, PostgreSQL and the restricted Docker
proxy images, then starts the Seed control-plane stack. Operators create TaskHub
nodes on the Seed Docker host. Offline bundles include the same images for
disconnected single-Seed installation.

The production default is `TASKHUB_WORKER_MODE=git`. Execution nodes enable the
Codex coding path and a user-namespace-compatible seccomp profile. They receive a
mount of an isolated model-runtime subdirectory containing only the
active coder credentials; Seed database, administrator, Git and session secrets
are not mounted into worker containers.

Build local images with `build-images.sh` or `build-images.ps1`. Set
`TASKHUB_REGISTRY` and `TASKHUB_PUSH=true` to publish the two TaskHub images.
The Dockerfiles use the official npm and Debian repositories by default;
constrained networks may pass `NPM_REGISTRY`, `DEBIAN_MIRROR` and
`DEBIAN_SECURITY_MIRROR` build arguments for trusted mirrors. npm package
integrity remains pinned by `package-lock.json`.
Create a self-contained, architecture-specific directory with `build-offline.sh`
or `build-offline.ps1`. Generated archives belong under ignored `dist/`; do not
commit image tar files.

## Operator entry points

| Action | Linux | Windows Docker Desktop |
| --- | --- | --- |
| Initialize | `./init.sh` | `.\init.ps1` |
| Back up | `./backup.sh` | `.\backup.ps1` |
| Upgrade | `./upgrade.sh VERSION [BUNDLE]` | `.\upgrade.ps1 -Version VERSION [-OfflineBundle BUNDLE]` |
| Restore | `./restore.sh BACKUP` | `.\restore.ps1 -BackupDirectory BACKUP` |

Backups contain the PostgreSQL database, TaskHub data volume, deployment
configuration (including the encryption master key), and rollback images. Keep
the backup directory on encrypted, access-controlled media. Restore verifies
file checksums and compares the master-key fingerprint with the database
identity before clearing any TaskHub data volume; it refuses mismatched sets.

Read `docs/deployment/ubuntu.md` and
`docs/deployment/windows-docker-desktop.md` before operating a release.

New installations start with HTTPS, Secure Cookie, session timeout/failure limiting and a
restricted Docker Socket Proxy. The initializer creates a short-lived self-signed bootstrap
certificate in `tls/`; replace it with an enterprise/public CA certificate before exposing Seed
beyond a trusted setup network. The controller itself no longer mounts `/var/run/docker.sock`.

After the first administrator login, configure the project Git authority in **System
Configuration → Advanced Settings → Manageable Platform Parameters → Git Repository
Service**. Supply the SSH user/host, port, authoritative bare-repository root, Seed checkout
root, and an optional dedicated private key. Test the draft values before saving. Saved
credentials are encrypted and take effect after Seed restarts; they are never returned to the
browser as plaintext.
