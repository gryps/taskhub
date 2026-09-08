# TaskHub V2

TaskHub V2 is a LangGraph-native AI software delivery control plane. The graph,
not an application-owned SQL state machine, controls workflow state and recovery.

## Current Scope

- One durable workflow thread per development run.
- Intake, planning, implementation, review, risk, and supervisor subgraphs.
- Native `interrupt()` plan approval and `Command(resume=...)` continuation.
- Memory checkpointer for development and PostgreSQL checkpointer for deployment.
- Plus/Pro device-auth accounts and GPT/DeepSeek/MiniMax API adapters.
- Ordered fallback with persisted cooldown, quota visibility, and preferred-provider recovery.
- Registered Git authorities, isolated per-run worktrees, real code changes, tests, patches,
  and commits.
- One-step project creation provisions a bare authority repository, clones a controller
  checkout, creates the initial commit, and registers the project.
- Human-approved publication, stale-base rebase, publication tests, fast-forward authority
  merge, and recoverable conflict reporting.
- Automatic supervisor-to-worker revision loops with bounded retries, durable revision
  commits, and an owner override at the configured limit.
- Authenticated execution nodes with capability-aware scheduling, per-node slots,
  sticky run assignment, failover, and visible node health.
- HTTP API, checkpoint history, SSE updates, and a small workflow timeline UI.

Provider secrets are loaded from `/home/gryps/.config/taskhub-v2/providers.env`
in deployment. This file must remain mode `600` and is never returned by the API.
ChatGPT account sessions remain in their Codex-managed `CODEX_HOME` directories.
Use `scripts/codex_account_login.sh plus|pro`; device authorization opens at
`https://auth.openai.com/codex/device`. OpenAI account and API traffic is forced
through the configured proxy. DeepSeek and MiniMax are forced direct.

Test and build execution can be distributed with `TASKHUB_TEST_RUNNER=scheduled`.
The controller sends a credential-filtered workspace archive to the selected node;
model credentials and the Git authority remain on the controller.

## Create A Project

The primary UI action is **Create project**. Enter a display name, select the project
type, confirm the generated project ID and base branch, then submit. In the deployed
topology TaskHub creates the bare authority repository on `192.168.31.3`, creates its
managed checkout on the controller, and adds it to the project selector. Attaching an
existing controller checkout remains available as an advanced operation.

Managed projects record an `authority_remote`. Publication fetches that remote,
rebases and retests when necessary, then pushes with a lease after owner approval.
A rejected push is a visible recoverable block and is never reported as successful.

## Governed Self Deployment

Self deployment is disabled by default and can be bound to exactly one registered
project with `TASKHUB_SELF_DEPLOY_PROJECT_ID`. After that project's run completes
authority publication, the run view exposes **Deploy and restart**. Deployment runs
in an independent user systemd unit so the control-plane restart cannot terminate it.

The executor verifies that the requested commit is the current authority branch,
requires non-empty project test commands, tests an archived release candidate, keeps
`.env` and `.venv`, restarts the configured service, and checks the health endpoint.
An unhealthy release restores the prior application files and restarts the service.
Deployment state is persisted outside the application directory.

## Run Locally

```bash
cp .env.example .env
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
make run
```

Open `http://localhost:8200`.

For PostgreSQL persistence:

```bash
docker compose up -d postgres
sed -i.bak 's/TASKHUB_CHECKPOINTER=memory/TASKHUB_CHECKPOINTER=postgres/' .env
make run
```

## Architecture Rules

- TaskHub maintainers never implement or repair managed-project code directly.
  Managed-project changes must come from a recorded TaskHub coding run; operators
  may repair only the platform, deployment, node environment, or configuration.
- Workflow transitions belong in `src/taskhub_v2/workflows/` only.
- API handlers call application services and never mutate workflow state directly.
- Providers and workers implement protocols; graphs do not contain vendor or host logic.
- Project-specific commands and paths do not belong in platform core.
- Python modules must remain below 400 lines; CI enforces this limit.
- Side effects must be idempotent because interrupted nodes restart from their beginning.

## Verification

```bash
make test
make lint
```
