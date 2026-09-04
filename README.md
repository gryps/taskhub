# Gryps LangGraph Control

LangGraph control plane deployed under `/home/gryps/apps/langgraph-control`.

## Endpoints

- `GET /health`
- `POST /invoke` with JSON body `{"input":"hello"}`
- `GET /planner/providers`
- `GET /planner/providers/health`
- `POST /planner/providers/{provider}/test`
- `POST /taskhub/tasks`
- `GET /taskhub/workflows/{workflow_id}`

All endpoints except `/`, `/static/*`, `/health`, and authentication require an
administrator session or bearer token.

## ChatGPT Account Runners

Plus and Pro use separate Codex homes and never receive API keys from TaskHub:

```bash
ssh -p 8022 -t gryps@192.168.31.31 \
  '/home/gryps/apps/langgraph-control/scripts/codex_account_login.sh plus'
ssh -p 8022 -t gryps@192.168.31.31 \
  '/home/gryps/apps/langgraph-control/scripts/codex_account_login.sh pro'
```

The login command uses device authentication. The controller detects login state
with `codex login status` and invokes `codex exec` with an ephemeral session, a
read-only sandbox, JSONL events, and a JSON output schema. Leave account model
overrides empty to use each account's Codex default model.

## Model Network Egress

- ChatGPT Plus, ChatGPT Pro, GPT API, and OpenAI-compatible OpenAI calls must use
  `OPENAI_PROXY_URL`. The controller fails closed when the proxy is absent.
- DeepSeek and MiniMax use explicit direct HTTP clients with `trust_env=false`,
  so process-level proxy variables cannot redirect those providers.
- Controller-to-worker LAN traffic remains direct through `NO_PROXY`.

Post-implementation workflow roles are disabled by default. Enable the controller-side
review, risk, and supervision runners with `WORKFLOW_ROLE_AUTOMATION_ENABLED=true`.
`WORKFLOW_ROLE_POLL_SECONDS`, `WORKFLOW_ROLE_RETRY_SECONDS`, and
`WORKFLOW_ROLE_MAX_ATTEMPTS` control polling and retries. Review and risk verdicts
advance or block the workflow automatically; supervision produces a recommendation
and leaves the final release action to a human operator.

The overview aggregates task heartbeat loss, long pending work, failed/blocked tasks,
stale workflows, role-model retry failures, provider degradation, and quota exhaustion.
Alert acknowledgement is persisted but does not change task or workflow state. Runtime
thresholds are configured with `TASKHUB_ALERT_PENDING_SECONDS` (default `1800`),
`WORKFLOW_ALERT_STALE_SECONDS` (default `1800`), and `TASKHUB_ALERT_MAX_RETRIES`
(default `3`). `WORKFLOW_ROLE_SLOW_SECONDS` sets the slow model-call threshold
(default `120`), and `WORKFLOW_ALERT_HISTORY_SECONDS` sets the successful-call
lookback window (default `86400`).

## Provider Recovery

Transient provider failures start a configurable cooldown. After the cooldown,
the preferred provider is probed again; a successful probe returns the current
request to that preferred provider. The default is:

```bash
PROVIDER_COOLDOWN_SECONDS=60
```

## Execution Nodes

- `192.168.31.24:8124`: Linux quality worker using an independent Git worktree.
- `192.168.31.34:8125`: Windows GUI worker for headed Edge/Playwright H5 checks.
- `192.168.31.31:8126`: Linux implementation worker using a separate WSL
  distribution and an independent Git branch copied from the `.17` authority.
- `192.168.31.31:8127`: second Linux implementation worker colocated with the
  controller environment and using its own Git workspace and branch.

Task dependencies are enforced by TaskHub. A task is claimable only after all
parents succeed; failed, blocked, or canceled parents block pending descendants.
Five-role planner runs persist a redacted, hash-linked evidence chain and stop at
the human approval gate.

## Production Pipelines

TaskHub pipelines isolate routing and workspaces for concurrent product work:

- `POST /taskhub/pipelines` creates a pipeline with a unique `workspace_id` and optional default worker.
- `PATCH /taskhub/pipelines/{id}` pauses or resumes dispatch with `state=paused|active`.
- Tasks may provide `pipeline_id`, `target_worker_id`, `workspace_id`, and `resource_keys`.
- Only the target worker can claim a routed task, and paused pipelines do not dispatch.
- A workspace automatically becomes an exclusive resource. Explicit keys such as `model:plus` or `gui:windows-31-34` protect other shared resources.
- Resource leases follow task heartbeats and are released on completion, failure, block, cancel, or lease expiry.

Default role routing follows the deployed hardware architecture:

- code tasks: `worker-31-31-implementation`
- test and model-review tasks: `worker-31-24-quality`
- H5 GUI inspection: `worker-31-34-gui`

Override these IDs with `TASKHUB_IMPLEMENTATION_WORKER`, `TASKHUB_QUALITY_WORKER`, and `TASKHUB_GUI_WORKER` when nodes change.

The production pipelines use explicit implementation workers:

- `电商开发 A 线` -> `worker-31-31-implementation-a`, branch `taskhub/implementation-31-31-01`
- `电商开发 B 线` -> `worker-31-31-implementation`, branch `taskhub/implementation-31-31-02`

## Run

```bash
./run.sh
```
