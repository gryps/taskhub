# TaskHub Release Kit

This directory is the source for the standard TaskHub delivery bundle. It is
separate from `deploy/seed`, which remains the Windows rapid-development setup.

## Runtime images

- `taskhub-seed:<version>`: Web/API/LangGraph control plane.
- `taskhub-node:<version>`: one role-gated image for execution, test and preproduction nodes.
- `postgres:16-alpine`: private controller database and checkpoint store.

TaskHub `0.1.0-alpha` is publicly available from either registry:

| Registry | Seed | Node |
| --- | --- | --- |
| GHCR | `ghcr.io/gryps/taskhub-seed:0.1.0-alpha` | `ghcr.io/gryps/taskhub-node:0.1.0-alpha` |
| Aliyun ACR | `crpi-kgqnka7pmz9f3sml.cn-hangzhou.personal.cr.aliyuncs.com/taskhub-v2/taskhub-seed:0.1.0-alpha` | `crpi-kgqnka7pmz9f3sml.cn-hangzhou.personal.cr.aliyuncs.com/taskhub-v2/taskhub-node:0.1.0-alpha` |

Both repositories allow anonymous pulls. Use the Aliyun ACR references when its
Hangzhou endpoint is faster from the deployment network.

Build local images with `build-images.sh` or `build-images.ps1`. Set
`TASKHUB_REGISTRY` and `TASKHUB_PUSH=true` to publish the two TaskHub images.
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

Read `docs/deployment/ubuntu.md` and
`docs/deployment/windows-docker-desktop.md` before operating a release.
