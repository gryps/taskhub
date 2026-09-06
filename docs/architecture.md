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

- A run ID is also the LangGraph `thread_id`.
- Every graph uses a durable production checkpointer.
- Human decisions enter only through `Command(resume=...)`.
- Code before `interrupt()` must be side-effect free or idempotent.
- A run ID selects one stable branch and worktree, making Worker retry idempotent.
- Publication is a separate gateway and only runs after a graph-owned human interrupt.
- Stale production lines rebase and retest before a fast-forward authority merge.
- Timeline records are graph state deltas accumulated by the parent graph.

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

An acceptance failure pauses at `acceptance_recovery` without rerunning the
coding model. At the revision limit, authenticated operators can submit database,
browser, or manual evidence through `POST /api/runs/{run_id}/acceptance`; the graph
then returns directly to review. API handlers never patch checkpoint state.

Project-specific paths, build commands, and product names are forbidden in
platform source and checked by an architecture test.

## Project Authority

Creating a project provisions a bare repository on the configured authority host and
a managed checkout on the controller. Runtime worktrees are derived from that checkout.
Before work begins, the controller fetches and fast-forwards to the authority remote.
After publication approval, the reviewed commit is pushed with an explicit lease so a
concurrent authority update becomes a recoverable block instead of being overwritten.

## Distributed Execution

The controller owns Git worktrees, commits, review, and publication. A remote node
receives a sanitized archive without `.git` or project credentials. A coding-enabled
node invokes its own configured model profiles and returns a validated change bundle;
test/build nodes return structured command results. A run keeps its assigned node while
it remains healthy and capable; transport failure permits controlled failover.
