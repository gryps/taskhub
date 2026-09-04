# Advanced Stage Completion

The advanced TaskHub stage adds governed multi-project operation on top of the
completed medium-stage pipelines. The controller remains the state authority;
workers execute bounded tasks and never receive controller or provider secrets.

## 1. Project Workspaces

- `taskhub_projects` registers one local Git authority host/root per project.
- Authority hosts are restricted by `TASKHUB_PROJECT_SOURCE_HOSTS`; the default
  is `192.168.31.17`.
- `taskhub_project_workspaces` binds a pipeline, worker, workspace ID, local path,
  branch, and baseline commit. Workspace IDs and branches cannot be reused.
- Once a project is registered, task creation rejects paused projects and unknown
  or mismatched workspaces.
- Context snapshots are immutable, versioned, hash-addressed baselines. The
  controller records supplied Git evidence but does not execute arbitrary SSH or
  shell commands against the authority host.

## 2. Dynamic Scheduling

Unbound tasks use a capability-specific worker pool. The scheduler chooses the
worker with the fewest running tasks, then the shortest pending queue, preserving
configured preference as the tie breaker. Explicit pipeline and worker routing
always takes precedence, so A/B production-line isolation is unchanged.

Configure pools with `TASKHUB_IMPLEMENTATION_WORKERS`,
`TASKHUB_QUALITY_WORKERS`, and `TASKHUB_GUI_WORKERS`.

## 3. Long-Term Context

Decisions, constraints, lessons, summaries, and risks are stored as deduplicated,
audited project memories. A bounded context pack combines the latest immutable
snapshot with the highest-importance active memories. Its hash and content are
fed to planning, review, risk, and supervision prompts. Memories can be disabled
without deleting their history.

## 4. Model Usage And Budget

Every attempted role/provider call records status, model, and available token
usage. API costs are calculated only from operator-provided rates:

```bash
LLM_COST_GPT_API_INPUT_PER_MILLION=0
LLM_COST_GPT_API_OUTPUT_PER_MILLION=0
LLM_COST_DEEPSEEK_API_INPUT_PER_MILLION=0
LLM_COST_DEEPSEEK_API_OUTPUT_PER_MILLION=0
LLM_COST_MINIMAX_API_INPUT_PER_MILLION=0
LLM_COST_MINIMAX_API_OUTPUT_PER_MILLION=0
```

Project budgets provide warning and optional hard-limit states. A hard limit
blocks new model-role calls but does not interrupt in-flight workers or alter
stored tasks. Account providers record calls even when their CLI does not expose
token counts.

## 5. Automatic Quality Evaluation

The advanced maintenance loop detects completed workflows and writes an immutable
quality evaluation from task completion, failed/blocked work, strict handoff
validation, and workflow state. It never releases a workflow. Operators can also
run and inspect evaluations through the API and Project Governance page.

## 6. Limited Autonomy

Autonomy is disabled by default. Policies may allow only these bounded actions:
task retry, expired-lease requeue, quality evaluation, and context refresh.
Each policy decision is persisted. Release, publication, credential changes,
project deletion, and production code application are permanently human-only,
even if a submitted policy attempts to allow them.

## API Surface

- `GET|POST /taskhub/advanced/projects`
- `GET|PATCH /taskhub/advanced/projects/{slug}`
- `POST /taskhub/advanced/projects/{slug}/workspaces`
- `POST /taskhub/advanced/projects/{slug}/snapshots`
- `GET /taskhub/advanced/projects/{slug}/context-pack`
- `POST|PATCH /taskhub/advanced/projects/{slug}/memories...`
- `GET /taskhub/advanced/scheduler`
- `GET /taskhub/advanced/usage`
- `PUT /taskhub/advanced/usage/{project}/budget`
- `POST|GET /taskhub/advanced/quality...`
- `GET|PUT|POST /taskhub/advanced/governance...`
- `GET /taskhub/advanced/dashboard`

## Acceptance

The stage is complete when migrations are applied, the existing project and both
implementation workspaces are registered, all tests pass under Python 3.12, the
controller is healthy, fixed pipeline routing remains intact, and governance
checks prove publication remains human-only.
