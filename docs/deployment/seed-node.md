# Seed Node Alpha

## Scope

The alpha seed package runs the current TaskHub Web/API controller and a durable
PostgreSQL checkpointer on one Docker host. It proves that a clean Docker Desktop
installation can start TaskHub without a host Python environment.

The controller can create execution, test, and preproduction node containers on
the Seed host or an admitted remote Linux Docker host from the Web UI. It uses the deterministic provider and local worker so the UI and
LangGraph workflow can be evaluated without copying provider credentials into the
image. The alpha image has only the Python/TaskHub runtime; full coding, Node.js,
browser, Git, and publication capabilities require the planned unified
`taskhub-node` release image with role-controlled startup profiles.

## Services

| Service | Responsibility | Published port |
| --- | --- | --- |
| `controller` | TaskHub Web, API, LangGraph orchestration | `8200/tcp` |
| `postgres` | Checkpoints and task index | none |
| Local Web-created node | Role-selected node agent and job workspace | none |
| Remote Web-created node | Role-selected node agent and job workspace | selected Agent port |

Both services use named Docker volumes. Deleting a container does not delete its
data; deleting the Compose volumes does.

The seed controller intentionally does not contain Git, Codex, Node.js, or
browser tooling. The controller image includes the OpenSSH client used only
for strict-fingerprint remote host admission; it contains no SSH private key or
preconfigured host trust. The installer also builds `taskhub-node:0.1.0-alpha`
from `deploy/node/Dockerfile`; it contains Git and Node.js/npm but does not yet
contain Codex CLI or browser runtimes.

## Web node lifecycle

Open **系统配置 → 工作节点**, select the Seed host or an admitted remote host,
enter a unique lower-case node ID, choose an allowed role and slot count, then
select **创建并启动**. Remote creation now runs in the background and reports
registry pull, SSH transfer, verification, container creation and Agent health
progress in the node table.

Configure an optional private-registry prefix, pull-through mirror prefix and
private-registry credentials under **系统配置 → 平台设置**. TaskHub first tries
those remote pull sources and the configured image reference. If none succeeds,
it exports the image with the Seed Docker Engine API and sends the tar stream over
strict-host-key SSH to remote `docker load`. This fallback requires the exact image
tag to exist on the Seed Docker host and enough temporary space inside the Seed
controller for one image archive. Registry credentials are encrypted at rest and
the remote Docker login uses an operation-local temporary configuration directory.

Before container creation, TaskHub verifies that the image is Linux, matches the
remote host CPU architecture, and has a usable digest or image ID. Digest-pinned
references are rejected when the resolved digest differs. SSH-transferred images
must have the same image ID on the Seed and remote engines.

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
addresses, image availability, model configuration, admitted hosts and healthy
nodes. Each incomplete step links to its canonical system-configuration form.
When every required condition passes, the wizard and running overview display
`系统已具备运行任务条件`.

If the work image cannot be pulled from a registry, the wizard can import a tar
archive produced by `docker save`. The browser supplies the expected image tag;
TaskHub streams the archive to the Seed Docker Engine, verifies that tag and
removes the temporary upload. Model and platform settings that require a restart
can be applied with the wizard's Seed restart action; Compose `unless-stopped`
then starts the controller again and the page waits for health recovery.

The encryption key protects Web-managed provider API keys stored in PostgreSQL.
It also protects SSH private keys entered through **系统配置 → 物理主机**. Before
admitting a host, configure a LAN-reachable Node Agent callback URL in platform
settings, detect the host fingerprint, verify it through a trusted channel, and
explicitly confirm it. TaskHub then checks SSH authentication, Linux hardware,
Docker permission and remote callback connectivity before saving the host.
It remains a deployment root secret: the Web UI can report whether encryption is
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
  the lifecycle API. Local nodes publish no host ports. Remote Alpha nodes publish
  a selected Agent port and receive the shared deployment node token; firewall the
  port to the Seed host and trusted LAN.
- A later hardened release should replace direct socket access with a restricted
  provisioning service or socket proxy before deployment outside a trusted host.
