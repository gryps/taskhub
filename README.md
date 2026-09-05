# Gryps LangGraph Control

LangGraph control plane deployed under `/home/gryps/apps/langgraph-control`.

中文操作手册：[`docs/TASKHUB_OPERATION_MANUAL_ZH.md`](docs/TASKHUB_OPERATION_MANUAL_ZH.md)

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

## Alert Notifications

System Settings can deliver unacknowledged alerts through a generic webhook,
WeCom bot, or DingTalk bot. Notifications are disabled by default. Webhook URLs
are stored in the protected environment file and returned to the browser only as
a mask. Delivery uses a direct HTTP client and does not inherit the OpenAI proxy.

- `TASKHUB_NOTIFICATION_ENABLED` enables delivery.
- `TASKHUB_NOTIFICATION_CHANNEL` is `generic`, `wecom`, or `dingtalk`.
- `TASKHUB_NOTIFICATION_MIN_SEVERITY` is `critical`, `warning`, or `info`.
- `TASKHUB_NOTIFICATION_POLL_SECONDS` defaults to `30`.
- `TASKHUB_NOTIFICATION_RETRY_SECONDS` defaults to `120`.
- `TASKHUB_NOTIFICATION_MAX_ATTEMPTS` defaults to `3`.
- `TASKHUB_PUBLIC_URL` optionally adds a control-center link to messages.

Successful delivery is unique per alert fingerprint and channel. Failed attempts
are persisted and retried; acknowledging an alert suppresses any pending delivery.

## Provider Recovery

Transient provider failures start an exponential cooldown. After the cooldown,
the preferred provider is probed again. A successful probe serves the current
request, while stable failback requires consecutive successful probes to avoid
route flapping. The defaults are:

```bash
PROVIDER_COOLDOWN_SECONDS=60
PROVIDER_RECOVERY_SUCCESSES=2
PROVIDER_PROBE_LOCK_SECONDS=30
```

## Escalation And Recovery

The operations policy escalates unacknowledged alerts and records every safe
recovery proposal. It defaults to disabled `dry_run`; active mode only executes
allowlisted actions. Expired leases can be requeued, while failed tasks and role
runs require explicit `auto_retry` or `auto_retry_roles` opt-in. Every proposal,
execution, skip, and failure is persisted in `taskhub_remediation_actions`.

Task handoffs use `taskhub.handoff/v1`. Handoff payloads and their hashes are
stored for review and traceability.

## Execution Nodes

- `192.168.31.24:8124`: Linux quality worker using an independent Git worktree.
- `192.168.31.34:8125`: Windows GUI worker for headed Edge/Playwright H5 checks.
  It does not discover or invoke unrelated collection and listing applications.
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

## Advanced Project Governance

The Project Governance page registers the `.17` local Git authority, binds each
pipeline to an isolated worker workspace/branch, versions project context, stores
long-term decisions and constraints, tracks model usage and budgets, evaluates
completed workflows, and applies an audited low-risk autonomy policy. Dynamic
routing is used only when a task has no explicit pipeline or worker. Publication,
release, credentials, and production code application always require a human.

See `docs/ADVANCED_STAGE_COMPLETION.md` for APIs, configuration, and acceptance
criteria.

## Integrated Project Operations

The Project Governance page now includes a four-step one-click onboarding wizard.
It validates the Git authority and implementation nodes, then creates the project,
A/B pipelines, isolated workspaces, source synchronization, budget, and governance
policy in one idempotent transaction. The same page launches the first five-role
workflow, shows centralized artifacts, model usage, devices, and governed actions,
and reports generic worker health and capabilities.

See `docs/INTEGRATION_STAGE_COMPLETION.md` for the completed eight-task scope and
security boundaries. Linux workers can publish files with
`scripts/publish_artifact.py`; the Windows GUI worker publishes task artifacts
automatically.

The original three-stage product plans and implementation records are preserved
under `docs/product-plans/`.

## Run

```bash
./run.sh
```
