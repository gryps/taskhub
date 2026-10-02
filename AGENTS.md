# TaskHub V2 Engineering Rules

This file is the project-level authority for work in this repository. It supplements the
machine-wide engineering rules and must not weaken their architecture, quality, security, or
continuous-convergence baseline.

## Product boundary

- TaskHub V2 is a LangGraph-native software-delivery control plane.
- Maintainers may change TaskHub itself, its deployment, node environments, configuration,
  diagnostics, tests, and documentation.
- Maintainers and assisting agents must not directly implement, repair, format, or commit code in
  projects managed by TaskHub. Managed-project changes must be produced by a recorded TaskHub run
  through its coding worker and verified by the configured gates.
- Reading a managed project and collecting non-mutating diagnostics is allowed. Bypassing TaskHub
  because a run is blocked, slow, or inconvenient is not allowed.

## Required context

Before changing TaskHub, read:

1. `docs/agent/project-context.md`
2. `docs/agent/memory-notes.md`
3. the relevant requirement, ADR, and architecture documents

Before changing a frontend surface, also read `docs/DESIGN.md`,
`docs/FRONTEND_ARCHITECTURE.md`, and the matching tests. `docs/frontend-design.md` remains the
detailed incumbent UI contract during the staged documentation migration.

## Architecture and ownership

- Preserve the dependency direction documented in `docs/ARCHITECTURE.md` and the ownership map in
  `docs/MODULES.md`.
- API routes adapt protocols and call services; they do not own workflow or domain rules.
- LangGraph checkpoint state is the authority for workflow position and continuation.
- External systems, Git, Docker, databases, model providers, and node agents remain behind their
  existing service, provider, worker, or persistence boundaries.
- New business capability belongs in a named module with a public contract. Do not add unowned
  code to generic `utils`, `common`, or page-level files.

## Continuous convergence

- Python source files must remain at or below 400 lines.
- New frontend source files must remain at or below 400 lines. Historical oversized frontend files
  are recorded in the architecture test as a temporary no-growth baseline and must shrink when
  touched.
- New Python functions must remain at or below 100 lines and cyclomatic complexity 15. Historical
  exceptions are explicit no-growth baselines in `tests/test_architecture.py`.
- A change that touches an oversized or over-complex area must not increase its baseline. Extract a
  coherent responsibility in the same batch whenever risk permits.
- Do not postpone tests, documentation, or structural correction to a final cleanup phase.

## Verification commands

- Python lint: `make lint`
- Architecture gates: `make architecture`
- Python tests: `make test`
- React tests: `make frontend-test`
- React type check and production build: `make frontend-build`
- Combined local gate: `make check`
- Frontend geometry or interaction changes additionally require real-browser checks at roughly
  1440, 768, and 390 pixels and the project-specific browser tests.

If a required command cannot run, report the exact reason and residual risk. Do not weaken a check,
delete a test, or expand an ignore list merely to obtain a passing result.

## Delivery safety

- Preserve API, SSE, route, permission, stable DOM, and deployment contracts unless the task
  explicitly changes them and the contract change is documented and tested.
- Never commit secrets, runtime data, databases, logs, caches, virtual environments, generated
  artifacts, or temporary release material.
- Deployment, image publication, Git push, and runtime mutation require explicit user authority and
  must follow the documented backup, validation, and rollback process.
- At handoff, report changed files, verification evidence, known exceptions, deployment state, and
  whether changes remain uncommitted.
