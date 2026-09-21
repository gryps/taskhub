# TaskHub V2 Architecture

## Authority

LangGraph checkpoint state is the only authority for workflow position, pending
actions, and continuation. API handlers cannot set a run stage directly.

## Runtime Boundaries

| Boundary | Responsibility |
| --- | --- |
| `workflows` | Graph topology, nodes, interrupts, and transitions |
| `services` | Start, inspect, and resume graph threads |
| `providers` | Model capabilities behind a protocol |
| `workers` | Execution capabilities behind a protocol |
| `execution` | Node registry, capability matching, slots, sticky assignment, and runners |
| `node_agent` | Authenticated workspace upload, coding, and isolated command execution |
| `persistence` | LangGraph checkpointer lifecycle |
| `api` | HTTP validation and presentation transport |
| `projects` | Authority repository provisioning, validation, and project registration |

Deterministic adapters remain available for tests. Production selects routed
model adapters and a Git worker without changing graph ownership. Provider
health is operational state, separate from workflow state, and only influences
which adapter receives the next call.

## Recovery Contract

- A blocked managed-project run is recovered by fixing TaskHub, its node environment,
  or project configuration and then rerunning the workflow. Platform maintainers do
  not edit the managed-project worktree as a substitute for the coding worker.
- `ExceptionCenterService` projects waiting, blocked and failed task-index entries into
  a unified operational view. It owns no second lifecycle or mutable recovery state;
  every recovery action remains defined by the workflow checkpoint and opens the
  existing task-detail action surface.
- `EvidenceCenterService` derives evidence completeness, integrity and lineage from the
  same run checkpoint. `ModelOperationsService` combines immutable model traces with
  the separate provider-health circuit state; neither service owns workflow state.
- A run ID is also the LangGraph `thread_id`.
- Every graph uses a durable production checkpointer.
- Human decisions enter only through `Command(resume=...)`.
- Code before `interrupt()` must be side-effect free or idempotent.
- A run ID selects one stable branch and worktree, making Worker retry idempotent.
- Publication is a separate gateway and runs automatically after graph-owned supervision succeeds.
- Stale production lines rebase and retest before a fast-forward authority merge.
- Timeline records are graph state deltas accumulated by the parent graph.

## Managed-Project Change Authority

The TaskHub coding worker is the sole writer for managed-project implementation
changes. Every such change must be attributable to a run and retain model, test,
artifact, and Git evidence. Human intervention at a workflow boundary is limited
to governance decisions and providing base infrastructure credentials through system
configuration. Routine deployment and evidence collection are platform responsibilities.
Intervention never authorizes a TaskHub maintainer
to implement the product change outside the workflow.

Maintainers may inspect managed projects without mutation to diagnose failures.
Convenience, retry exhaustion, and schedule pressure are not exceptions to the
no-modification boundary.

## Dependency Direction

```text
api -> services -> workflows -> domain
                       |       -> provider protocol
                       +------ -> worker protocol
persistence -> LangGraph checkpoint implementation
```

## Acceptance Evidence

Implementation tests and delivery acceptance are separate gates. A project can
declare `acceptance_commands`; TaskHub schedules them with the `acceptance`
workload against the task workspace and persists command output plus artifact
digests in LangGraph state. Review, risk, and supervision receive both the code
change and structured acceptance evidence.

An execution-environment failure pauses at `acceptance_recovery` without rerunning the
coding model. Contract and suite defects are returned automatically to the coding worker
while a revision remains available. For a configured preproduction environment, TaskHub
runs the contract's deployment command, verifies the candidate commit, environment identity,
and database revision from the target health endpoint, and only then dispatches browser
acceptance against that exact target. API handlers never patch checkpoint state.

Project-specific paths, build commands, and product names are forbidden in
platform source and checked by an architecture test.

## Project Authority

Creating a project provisions a bare repository on the configured authority host and
a managed checkout on the controller. Runtime worktrees are derived from that checkout.
Before work begins, the controller fetches and fast-forwards to the authority remote.
After supervision approval, the reviewed commit is pushed with an explicit lease so a
concurrent authority update becomes a recoverable block instead of being overwritten.

Project startup readiness is owned by `ProjectPreflightService`, not duplicated in the
browser. Its read-only report aggregates repository reachability, effective model
configuration, online local execution capability, active contract requirements,
quality commands, and declared acceptance capabilities. The UI may navigate to a
repair surface, but only the server report enables a new run.

## Single-Seed Node Execution

The controller owns Git worktrees, commits, review, and publication. Role-selected node
containers run only in the Docker Engine connected to the Seed. A node receives a
sanitized archive without `.git` or project credentials. A coding-enabled node invokes
its isolated model profiles and returns a validated change bundle; test/build nodes
return structured command results. A run keeps its assigned node while it remains
healthy and capable; container or Agent failure permits controlled failover to another
eligible local node.

Command results are persisted by job ID, workspace digest, and request content on the
execution node. Reconnected controllers retrieve the same result instead of repeating
the command. After a controller restart, graph checkpoints still marked running are
replayed automatically when they have a next node and are not waiting for owner input.

Physical-host inventory, SSH admission, cross-host image transfer, remote-node upgrade
and migration APIs are not part of the active product. Their historical persistence and
service modules remain only as an upgrade/rollback compatibility boundary.
