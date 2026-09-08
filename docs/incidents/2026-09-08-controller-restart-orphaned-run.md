# Controller Restart Left A Run Falsely Running

## Symptom

A run remained at `stage=implementation`, `status=running`, with no error and no
active controller or execution-node process.

## Cause

The controller restarted while waiting for a synchronous remote execution request.
LangGraph had durably recorded the next implementation node, but the in-memory graph
coroutine disappeared. The execution node continued after the HTTP client disconnected,
then discarded its result because results were returned only in the HTTP response.

## Correction

- Execution nodes persist successful command results atomically per job and request.
- Re-uploading an identical workspace preserves the persisted result.
- Repeating an identical execution request returns the persisted result without rerunning.
- On PostgreSQL startup, the controller schedules recovery for running checkpoints that
  have a next graph node and no pending owner action.

Managed-project code is not modified as part of this recovery. The graph coding worker
remains the sole writer of managed-project implementation changes.
