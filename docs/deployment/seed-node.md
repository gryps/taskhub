# Seed Node Alpha

## Scope

The alpha seed package runs the current TaskHub Web/API controller and a durable
PostgreSQL checkpointer on one Docker host. It proves that a clean Docker Desktop
installation can start TaskHub without a host Python environment.

The controller can create execution, test, and preproduction node containers from
the Web UI. It uses the deterministic provider and local worker so the UI and
LangGraph workflow can be evaluated without copying provider credentials into the
image. The alpha image has only the Python/TaskHub runtime; full coding, Node.js,
browser, Git, and publication capabilities require the planned unified
`taskhub-node` release image with role-controlled startup profiles.

## Services

| Service | Responsibility | Published port |
| --- | --- | --- |
| `controller` | TaskHub Web, API, LangGraph orchestration | `8200/tcp` |
| `postgres` | Checkpoints and task index | none |
| Web-created node | Role-selected node agent and job workspace | none |

Both services use named Docker volumes. Deleting a container does not delete its
data; deleting the Compose volumes does.

The seed controller intentionally does not contain Git, SSH, Codex, Node.js, or
browser tooling. Web-created alpha nodes prove lifecycle, registration, scheduling
and health reporting; those additional capabilities belong in the unified
`taskhub-node` release image rather than the Seed controller.

## Web node lifecycle

Open **系统配置 → 节点容器**, enter a unique lower-case node ID, choose a role and
slot count, then select **创建并启动**. TaskHub creates the container on its private
Compose network and atomically adds it to `nodes.json`.

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
Fernet configuration-encryption key, builds the application image, starts both
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

The encryption key protects Web-managed provider API keys stored in PostgreSQL.
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
  the lifecycle API. Node containers publish no host ports and receive only the
  shared node communication token.
- A later hardened release should replace direct socket access with a restricted
  provisioning service or socket proxy before deployment outside a trusted host.
