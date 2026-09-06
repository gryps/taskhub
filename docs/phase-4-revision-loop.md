# Supervised Revision Loop

## Business Flow

1. Supervisor approval continues to publication approval.
2. Supervisor rejection records its summary and every reason as revision feedback.
3. While the project revision budget remains, LangGraph automatically returns the same run
   to implementation without asking the owner to create another task.
4. The coding model edits the existing run worktree, runs the project tests, and creates a
   numbered revision commit.
5. Review, risk, and supervision run again against the complete diff from the original base.
6. At the revision limit, the graph pauses and asks the owner to allow one more revision or
   cancel the run.

## Recovery Contract

- `revision_count` belongs to checkpoint state and survives service restarts.
- Every revision commit contains `TaskHub-Run` and `TaskHub-Revision` trailers.
- Re-entering a completed revision recovers its commit instead of invoking the model twice.
- Revision artifacts use `change-rN.patch`; earlier evidence remains available.
- Operational retries do not consume the supervised revision budget.
