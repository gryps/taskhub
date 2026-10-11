# ADR 0006: Live DAG state and terminal archive

## Status

Accepted on 2026-10-11.

## Context

LangGraph checkpoints describe workflow position, while production records describe task, batch and
attempt execution. A long-running scheduler call can update production records for many minutes before
the next graph checkpoint. Project clients that read only the checkpoint therefore reported pending
tasks after execution had started. Archiving also hid the task-index row without stopping the active
workflow or terminalizing production records, leaving historical runs marked as running.

The planner also inferred verification work from any matching word in the full step. An implementation
step that mentioned audit logging could become a verification task, and every verification task depended
on every implementation task, so a preflight check could be scheduled last.

## Decision

1. Run reads overlay checkpoint workflow data with the current production-store execution plan, tasks,
   batches and latest snapshot. The production store is authoritative for live DAG execution facts.
2. Archiving an active run cancels its controller task and terminalizes its active production plan,
   tasks, attempts and batches. The task index exposes the run as `cancelled`; already terminal runs keep
   their completed, rejected or failed result when archived.
3. Node selection is reported as soon as a scheduler acquires a node and is persisted on the active task
   and attempt before the remote command finishes.
4. Planner phase inference gives the heading and explicit implementation verbs precedence over incidental
   verification words. Preflight verification gates subsequent implementation; final verification depends
   only on preceding implementation work.
5. An unchanged production task uses a stable node job identity across attempts. A quality timeout is a
   terminal attempt result and is not repeated blindly without a changed command, workspace or policy.

## Consequences

- API and CLI progress no longer wait for a graph checkpoint to show current task execution.
- Archived active runs no longer revive during recovery or remain visually running.
- Cancellation of the controller request cannot guarantee termination of an already detached external
  process on every legacy node. A dedicated node cancel protocol remains a future hardening item.
- Existing archived rows are not rewritten automatically; their next indexed refresh or an explicit data
  repair can apply the new terminal representation.
