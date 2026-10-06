# TaskHub V2 Architecture

## System purpose and authority

TaskHub V2 is a LangGraph-native control plane for turning a product requirement into a governed,
repeatable software-delivery run. LangGraph checkpoint state is the sole authority for workflow
position, pending actions, and continuation. HTTP handlers and browser clients may request actions,
but they must not manufacture or directly patch workflow state.

The coding worker is the sole writer of implementation changes in managed projects. TaskHub
maintainers may change this platform and inspect managed repositories, but they may not bypass the
platform by directly completing a managed project's work.

## Runtime boundaries

| Boundary | Responsibility | May depend on |
| --- | --- | --- |
| `api` | HTTP/SSE protocol adaptation, validation, authorization, presentation transport | services, domain contracts |
| `services` | Application use cases and orchestration outside graph topology | domain, persistence/public adapters |
| `workflows` | LangGraph topology, nodes, interrupts, transitions | domain, provider/worker protocols |
| `domain` | Stable records, rules, state, and public business contracts | standard library, Pydantic base types |
| `providers` | Model-provider adapters behind capability protocols | provider SDKs, domain contracts |
| `workers` | Coding, testing, acceptance, publication capabilities | execution and external adapters |
| `execution` | Node registry, matching, slots, assignments, runners | node-agent/public service contracts |
| `external_windows_nodes` | Native Windows SSH admission, Agent installation and scheduler registration | node registry and per-node credential vault |
| `node_agent` | Authenticated workspace upload and isolated execution | operating system and container adapters |
| `persistence` | Checkpoints and production-record storage | PostgreSQL/LangGraph implementations |
| `projects` | Authority repository provisioning and registry | Git/SSH adapters |

The intended direction is:

```text
browser -> api -> services -> workflows -> domain
                         |-> providers / workers / execution
                         |-> persistence / projects
```

Dependencies should be explicit and one-way. API, browser, infrastructure, and persistence details
must not leak into domain models. Cross-module callers use public service or domain contracts rather
than reaching into another module's internal state.

## Core data ownership

- Workflow checkpoints own stage, pending action, continuation, and graph timeline.
- `ProjectContract` owns the versioned project boundary, commands, documentation, and frozen
  engineering-policy binding.
- `EngineeringPolicy` owns global rule versions. `PolicyException` owns bounded, approved waivers.
- Execution plans own task dependency and batch facts; the browser only renders these server facts.
- Provider health is operational state and does not replace workflow state.
- Project repository authority remains the configured Git remote; worktrees are reproducible run
  workspaces, not a second source of truth.

Detailed module-level ownership and public interfaces are listed in `docs/MODULES.md`.

## Human and agent authentication

Browser users authenticate with signed, server-revocable session cookies and CSRF protection.
Development agents authenticate through a browser-approved pairing and a separately revocable
Bearer credential. `security.agent_access` owns pairing state, credential digests, expiry and
revocation; API middleware maps either authentication mechanism into the same RBAC principal before
calling application services. Bearer authentication never falls back to an ambient browser session,
and connection metadata contains only the path to a host-protected credential file. ADR 0004 records
the security decision and rejected alternatives.

## Governance flow

An active engineering policy is compiled into a new project-contract draft. The contract freezes the
policy ID, version, digest, applicable rule IDs, and worker instructions. Execution plans, tasks, and
coding context retain that binding so later policy activation cannot silently mutate active work.

After every integrated batch, the scheduler runs contract and global-governance checks and stores the
result on the execution batch. Final acceptance repeats the full contract gate and declared quality
commands. A policy exception can downgrade only named failures within an approved scope and expiry.

## Two-stage activation

Seed onboarding owns only platform-wide infrastructure readiness. Project activation is a separate
application service that composes the project registry, current project contract and project
preflight report after a repository has been attached. It does not persist a second readiness
state. Project-specific Windows acceptance is conditional on the repository contract or project
quality declaration and is never a Seed-global dependency.

## Recovery and idempotency

- A run ID is also its LangGraph thread ID and selects one stable branch/worktree.
- Human decisions enter through `Command(resume=...)`; code before `interrupt()` is side-effect free
  or idempotent.
- Node jobs are keyed by job ID, workspace digest, and request content so reconnects retrieve prior
  results instead of repeating commands.
- Stale production lines rebase and retest before an authority fast-forward.
- A blocked managed-project run is repaired by correcting TaskHub, configuration, infrastructure, or
  its generated project contract, then retrying through the workflow.

## Delivery and deployment boundary

The controller owns worktrees, review, evidence, and publication. Execution nodes receive sanitized
archives without `.git` or project credentials and return structured results. Formal deployment uses
the release Compose and documented backup/rollback procedures; runtime secrets, databases, volumes,
provider state, and generated artifacts remain outside source control.

## Architecture verification

`tests/test_architecture.py` enforces standard documentation, source-file budgets, Python function and
complexity no-growth baselines, import-cycle absence, and platform/business decoupling. Historical
exceptions are explicit migration baselines: they may shrink but may not grow. New exceptions require
an ADR with reason, risk, control, and removal condition.

This uppercase file is the canonical architecture authority.
