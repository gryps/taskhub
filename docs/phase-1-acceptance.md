# Phase One Acceptance

## Delivered

- LangGraph parent graph and six responsibility subgraphs.
- Automatic plan-to-implementation and supervision-to-publication transitions.
- Development memory checkpointer and production PostgreSQL checkpointer.
- Restart recovery through a new database connection and graph instance.
- Provider and worker protocols, deterministic test adapters, and an OpenAI Responses adapter.
- Run creation, detail, recovery, history, health, and SSE endpoints.
- Browser workflow timeline with explicit pending human action only for recovery and unresolved decisions.
- Architecture tests for module size and business-project coupling.

## Hard Acceptance Scenarios

| Scenario | Expected result |
| --- | --- |
| Start a requirement | Continues from planning through implementation and publication without routine approval stops |
| Implementation or publication failure | Stops at a recoverable blocked state with retry/cancel actions |
| Supervision rejects | Returns to implementation within the revision limit |
| Resume twice | Returns conflict instead of repeating execution |
| Recreate runtime | Restores completed, blocked, or running state from PostgreSQL |
| Inspect history | Shows automatic stage boundaries and recovery checkpoints |

## Explicitly Deferred

- Production model credentials, real-call verification, and Plus/Pro account runners.
- Git authority, isolated worktrees, patches, and test artifacts.
- Remote Linux and Windows execution.
- Multiple concurrent production lines and model fallback.

These are phase-two features. The phase-one worker does not modify source code.
