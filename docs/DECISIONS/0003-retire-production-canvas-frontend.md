# ADR 0003: Retire the production canvas frontend

- Status: accepted
- Date: 2026-10-03

## Context

The standalone React production canvas duplicated a frontend delivery toolchain and added build,
deployment, cache, responsive, and accessibility obligations. The product owner has decided that the
visual canvas is not part of the current TaskHub product direction.

The server-side production topology also constrains scheduler eligibility and preserves existing
topology records. Removing it in the same batch would change runtime behavior and stored-data
compatibility beyond the requested UI retirement.

## Decision

Remove the `/canvas/` route, its navigation entry, the `taskhub-web` application, Docker/CI/npm build
steps, and canvas-specific browser tests. Keep the server-side topology domain, persistence, API, and
scheduler integration as an internal compatibility capability until a separate product decision
explicitly changes that runtime contract.

TaskHub now has one server-delivered frontend. Any future standalone frontend or topology editor must
start with a new product and architecture decision; the deleted canvas implementation is not a dormant
supported surface.

## Consequences

- The runtime image and CI no longer install or build a second frontend application.
- Users cannot create or edit production topologies through a visual canvas.
- Existing topology APIs and active scheduler restrictions continue to operate.
- Canonical product, design, architecture, skill, and verification documentation must not instruct
  maintainers to use or test `/canvas/`.

## Follow-up condition

If server-side topology is no longer required, remove its API, service, persistence, schema, scheduler
integration, tests, and stored-data migration in a dedicated compatibility-reviewed change.
