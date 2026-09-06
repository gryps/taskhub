# Governed Publication

## Business Flow

1. The supervisor accepts or rejects the reviewed implementation.
2. An accepted change pauses at a native LangGraph publication approval interrupt.
3. The owner can inspect the plan, changed files, tests, and supervision decision.
4. Approval starts publication; rejection leaves the authority branch unchanged.
5. Publication verifies that the reviewed run branch was not changed out of band.
6. If the authority advanced, TaskHub rebases the run branch and reruns project tests.
7. A passing branch is merged into the checked-out authority branch with `--ff-only`.
8. Conflicts, dirty authority state, test failures, and concurrent updates become visible
   blocking reasons with explicit retry or cancel choices.

## Safety Contract

- Publication only accepts a workspace derived from the configured project and run ID.
- The authority repository must be clean and checked out on the registered base ref.
- Reviewed patch identity must remain stable if a previous publication attempt rebased it.
- Tests always execute before the authority ref moves.
- Fast-forward merge prevents hidden merge commits and non-linear authority history.
- Publication is retryable after process or checkpoint recovery.

## Acceptance Evidence

- Production run: `75d24f28-a147-4e6f-9879-b5dc6e8ada8e`
- Reviewed implementation commit: `64cb3271ea6a2b9eb4e89d8bcd48d5507419af42`
- Authority stayed at `a4591a4` until publication approval.
- Publication tests passed and `main` then fast-forwarded to `64cb327`.
- After a service restart, PostgreSQL restored the completed publication result.
