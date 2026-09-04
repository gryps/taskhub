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

Task dependencies are enforced by TaskHub. A task is claimable only after all
parents succeed; failed, blocked, or canceled parents block pending descendants.
Five-role planner runs persist a redacted, hash-linked evidence chain and stop at
the human approval gate.

## Run

```bash
./run.sh
```
