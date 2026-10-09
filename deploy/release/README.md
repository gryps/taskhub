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
Builds reuse the local BuildKit/base-image cache by default. Set
`TASKHUB_PULL_BASE_IMAGES=true` only when an intentional base-image refresh is
required; routine source releases must not re-download unchanged dependencies. When Docker Hub
metadata is unavailable but the previous TaskHub images are present locally, set
`TASKHUB_REUSE_RUNTIME_VERSION` to that version. The build verifies that dependency, base-image and
entrypoint definitions have not changed, then overlays and reinstalls the current source without
contacting a base-image registry; it fails closed when a full rebuild is required.
For a verified image built on the deployment host, `TASKHUB_USE_LOCAL_IMAGES=true upgrade.sh VERSION`
skips registry pulls while retaining the normal backup, health check and automatic rollback path.
When a complete backup was just created and verified, `TASKHUB_VERIFIED_BACKUP` may point to that
exact directory so a retry re-verifies and reuses it instead of exporting the same images again.
Never point it at an interrupted or unverified directory.
When creating a new backup without changing any of the three runtime image references,
`TASKHUB_REUSE_IMAGE_BACKUP` may point to a prior verified backup. The backup tool first verifies
that recovery set and requires exact Seed, Node and PostgreSQL image matches, then reuses its image
archive while still capturing fresh PostgreSQL and TaskHub volume data. This avoids repeatedly asking
Docker Desktop to export the same large images.
The Dockerfiles use the official npm and Debian repositories by default for managed-project previews;
constrained networks may pass `NPM_REGISTRY`, `DEBIAN_MIRROR` and
`DEBIAN_SECURITY_MIRROR` build arguments for trusted mirrors. npm package
integrity remains pinned by each managed project's lock file.
Create a self-contained, architecture-specific directory with `build-offline.sh`
or `build-offline.ps1`. Generated archives belong under ignored `dist/`; do not
commit image tar files.

## Operator entry points

| Action | Linux | Windows Docker Desktop |
| --- | --- | --- |
| Prepare host | `./prepare-ubuntu.sh` | `.\prepare-windows.ps1` |
| Preflight | `./preflight.sh` | `.\preflight.ps1` |
| Initialize | `./init.sh` | `.\init.ps1` |
| Configure TLS | `./configure-tls.sh` | `.\configure-tls.ps1` |
| Verify | `./verify.sh` | `.\verify.ps1` |
| Back up | `./backup.sh` | `.\backup.ps1` |
| Verify backup | `./verify-backup.sh BACKUP` | `.\verify-backup.ps1 -BackupDirectory BACKUP` |
| Upgrade | `./upgrade.sh VERSION [BUNDLE]` | `.\upgrade.ps1 -Version VERSION [-OfflineBundle BUNDLE]` |
| Restore | `./restore.sh BACKUP` | `.\restore.ps1 -BackupDirectory BACKUP` |
| Package online kit | `./package-online.sh` | `.\package-online.ps1` |

Backups contain the PostgreSQL database, TaskHub data volume, deployment
configuration (including the encryption master key), and rollback images. Every
backup runs the read-only verifier before it is declared complete; the verifier
can also be rerun later without changing TaskHub data. Keep
the backup directory on encrypted, access-controlled media. Restore verifies
file checksums and compares the master-key fingerprint with the database
identity before clearing any TaskHub data volume; it refuses mismatched sets.

Read `docs/deployment/ubuntu.md` and
`docs/deployment/windows-docker-desktop.md` before operating a release.

Host preparation is intentionally separate from `init`: the preparation tools
make operating-system changes only when the operator explicitly requests an
installation flag. Preflight and verification are read-only. Online release
packages contain Compose, lifecycle tools, examples, documentation and
checksums, but never `.env`, backups, credentials or container images.

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

To connect Codex or another development agent, run `connect-agent.py` from this release kit with
the Seed URL, trusted CA file, private token-file path and optional
connection JSON path. The helper displays a short pairing code. Review and approve that code under
**System Configuration → Advanced Settings → Users, Permissions and Access Security**. It writes the
Bearer credential directly to the protected token file and never prints or embeds it in the
connection JSON. Windows applies an explicit current-user ACL; Unix-like systems use mode `0600`.

Verify an existing connection with `check-agent-connection.py --connection-file PATH`. The helper
uses the descriptor's exact `health_url` and `auth_status_url`; it never derives endpoint paths from
`base_url`. It also applies the declared CA, direct-proxy policy and Bearer token file, while its
output contains only health, authentication and role status.

Health and authentication do not identify a project run. Use the release kit's project client as
the standard development-agent entry point:

```bash
python3 taskhub-project-client.py --connection-file PATH readiness \
  --project PROJECT_ID
python3 taskhub-project-client.py --connection-file PATH ensure-run \
  --project PROJECT_ID --production-line LINE
```

`readiness` is read-only and reports the selected product specification, active project contract
and stable blocker codes with the required next action. Run it when connection checks pass but no
run id is available. It does not approve governance records or weaken the creation gate.
`ensure-run` returns the one non-terminal run already registered for that project and production
line. It creates a run only when none exists, and only when the selected product specification is
approved and a project contract is active. Multiple matches fail closed instead of choosing or
creating a run. Use `projects`
and `runs --project PROJECT_ID` for discovery, `status --run-id RUN_ID` for a bounded agent summary
(`--full` for the complete state), and
`resume --run-id RUN_ID --decision DECISION --comment TEXT` only when an explicit recovery decision
is intended. Use `archive --run-id RUN_ID` after a run reaches a terminal state and should leave
the active task list. `create-run` is available for an intentional new run and accepts explicit
`--spec-id/--spec-version` when the current approved specification must not be used. All commands
reuse the connection descriptor's TLS trust, proxy mode and protected Bearer token reference; they
never print the credential.
