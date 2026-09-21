# Seed Node Alpha

## Scope

The alpha seed package runs the current TaskHub Web/API controller and a durable
PostgreSQL checkpointer on one Docker host. It proves that a clean Docker Desktop
installation can start TaskHub without a host Python environment.

The controller can create execution, test, and preproduction node containers on
the Seed-connected Docker Engine from the Web UI. It uses the deterministic provider and local worker so the UI and
LangGraph workflow can be evaluated without copying provider credentials into the
image. The historical alpha Seed image has only the Python/TaskHub runtime. The
standard release now builds a unified `taskhub-node` image with role-controlled
workloads, Git, Python/pytest, Node.js/npm, Playwright's Python package and Codex
CLI. Browser binaries and project-specific databases remain explicit task
capabilities rather than universal image requirements.

## Services

| Service | Responsibility | Published port |
| --- | --- | --- |
| `controller` | TaskHub Web, API, LangGraph orchestration | `8200/tcp` |
| `postgres` | Checkpoints and task index | none |
| Local Web-created node | Role-selected node agent and job workspace | none |

Both services use named Docker volumes. Deleting a container does not delete its
data; deleting the Compose volumes does.

The standard Seed controller contains Codex CLI for ChatGPT device authorization,
but not Git, Node.js or browser tooling. The installer also builds `taskhub-node:0.1.0-alpha`
from `deploy/node/Dockerfile`; it contains the common role runtime but does not
preinstall browser binaries.

## Web node lifecycle

Open **系统配置 → 工作节点**, enter a unique lower-case node ID, choose a role and
slot count, then select **创建并启动**. Creation reports image availability,
container creation and Agent health progress in the node table.

Configure an optional private-registry prefix, pull-through mirror prefix and
private-registry credentials under **系统配置 → 平台设置**. Online initialization
preloads the configured Node image into the Seed Docker Engine. For disconnected
installation, import the verified architecture-specific offline bundle or use the
wizard's `docker save` archive import. Registry credentials are encrypted at rest.

| Role | Registered workloads |
| --- | --- |
| Execution | `build`, `coding` |
| Test | `test`, `acceptance` |
| Preproduction | `build`, `test`, `acceptance` |

Stopping a container keeps its registration and data. Removing it deletes the
container and registration but deliberately retains its named data volume.

## Windows Docker Desktop

From the repository root in PowerShell:

```powershell
.\deploy\seed\start-seed.ps1
```

The script creates `deploy/seed/.env` once, generates random credentials and a
Fernet configuration-encryption key, builds the Seed and unified node images, starts both
services, and waits for `/api/health`. Existing environment files are upgraded
in place with a new encryption key if that field is absent.

If Docker Hub is slow or unavailable in mainland China, preload the two public
base images through the verified DaoCloud prefix mirror before starting:

```powershell
.\deploy\seed\pull-public-base-images.ps1
```

The helper restores the user's Docker CLI configuration after pulling and tags
the images with the standard names used by the Dockerfile and Compose file. The
mirror is a transport fallback, not a change to the runtime image identities.

Open `http://HOST-IP:8200`. On the first visit, the login page asks for the
deployment bootstrap token and a new administrator password. Retrieve
`TASKHUB_ADMIN_TOKEN` from the host-local `deploy/seed/.env` file and use it only
for this initial setup. Later logins accept the administrator password instead.
Never commit or copy the `.env` file into an image.

After the first password is set, an incomplete Seed opens the first-run wizard.
It checks Docker CPU and memory capacity, TaskHub persistent-disk space, managed
addresses, image availability, model configuration and healthy local nodes. Each
incomplete step links to its canonical system-configuration form.
When every required condition passes, the wizard and running overview display
`系统已具备运行任务条件`.

If the work image cannot be pulled from a registry, the wizard can import a tar
archive produced by `docker save`. The browser supplies the expected image tag;
TaskHub streams the archive to the Seed Docker Engine, verifies that tag and
removes the temporary upload. Model and platform settings that require a restart
can be applied with the wizard's Seed restart action; Compose `unless-stopped`
then starts the controller again and the page waits for health recovery.

The encryption key protects Web-managed provider API keys stored in PostgreSQL and
per-node credentials stored in the persistent TaskHub data volume. It remains a
deployment root secret: the Web UI can report whether encryption is
available but cannot read or replace this key. Back up the `.env` file securely;
losing this key makes previously stored provider credentials unrecoverable.

The password is stored as a salted `scrypt` hash in the persistent TaskHub data
volume at `/var/lib/taskhub/config/admin-password.json`; plaintext is never
written to the volume.

## Acceptance

```powershell
docker compose --project-directory .\deploy\seed --env-file .\deploy\seed\.env `
  -f .\deploy\seed\compose.yaml ps
Invoke-RestMethod http://127.0.0.1:8200/api/health
```

Expected health response:

```json
{"status":"ok","orchestrator":"langgraph"}
```

Restart the stack and verify the same endpoint again:

```powershell
docker compose --project-directory .\deploy\seed --env-file .\deploy\seed\.env `
  -f .\deploy\seed\compose.yaml restart
```

## Security Boundary

- PostgreSQL is reachable only on the Compose network.
- The image contains no provider key, session, SSH private key, or managed project.
- HTTP is acceptable only for this LAN alpha. TLS and secure cookies are required
  before exposing the controller outside a trusted network.
- Web lifecycle management mounts the Docker socket into the authenticated
  controller. Access to that socket is equivalent to Docker-host administrator
  authority; keep this alpha on a trusted LAN and never make its HTTP port public.
- Only containers carrying TaskHub's managed label are listed or operated on by
  the lifecycle API. Local nodes publish no host ports. Each managed node receives
  a different encrypted-at-rest credential; plaintext is injected only into that node.
- A later hardened release should replace direct socket access with a restricted
  provisioning service or socket proxy before deployment outside a trusted host.
