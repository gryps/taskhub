# Phase One Acceptance

## Delivered

- LangGraph parent graph and six responsibility subgraphs.
- Native plan approval with `interrupt()` and `Command(resume=...)`.
- Development memory checkpointer and production PostgreSQL checkpointer.
- Restart recovery through a new database connection and graph instance.
- Provider and worker protocols, deterministic test adapters, and an OpenAI Responses adapter.
- Run creation, detail, approval, history, health, and SSE endpoints.
- Browser workflow timeline with explicit pending human action.
- Architecture tests for module size and business-project coupling.

## Hard Acceptance Scenarios

| Scenario | Expected result |
| --- | --- |
| Start a requirement | Stops at `plan_approval`; worker has not run |
| Approve | Continues through worker, review, risk, and supervisor |
| Reject | Ends as rejected; worker never runs |
| Resume twice | Returns conflict instead of repeating execution |
| Recreate runtime | Restores waiting run from PostgreSQL and continues |
| Inspect history | Shows checkpoint at the approval boundary |

## Explicitly Deferred

- Production model credentials, real-call verification, and Plus/Pro account runners.
- Git authority, isolated worktrees, patches, and test artifacts.
- Remote Linux and Windows execution.
- Multiple concurrent production lines and model fallback.

These are phase-two features. The phase-one worker does not modify source code.
