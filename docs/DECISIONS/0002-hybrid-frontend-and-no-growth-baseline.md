# ADR 0002: Retain the hybrid frontend under no-growth migration gates

- Status: superseded by ADR 0003
- Date: 2026-10-03

## Context

TaskHub's native operations console is stable and deeply integrated with FastAPI, while the production
canvas already justifies React, TypeScript, React Flow, and a build pipeline. Several incumbent native
assets and `TopologyApp.tsx` exceed the new global file-size baseline. A one-shot rewrite would combine
visual, behavioral, deployment, and cache risk and could obscure regressions.

## Decision

Retain both frontend delivery paths for now. Register each historical oversized file at its current
line count in the repository architecture test. The baseline is a ceiling, not an allowance: files may
shrink but may not grow, and no new frontend source file may exceed 400 lines.

When a feature touches an oversized file, extract a coherent responsibility behind the existing API,
DOM, or component contract and reduce the recorded ceiling in the same batch. Shared network/error
handling is the first native extraction target; topology editing, persistence actions, and panels are
the first React extraction boundaries.

## Consequences

- CI immediately prevents further concentration without forcing a high-risk rewrite.
- The product temporarily keeps two frontend toolchains and must verify both.
- Migration progress is measurable through decreasing ceilings.
- Removing or increasing a ceiling requires a new or superseding ADR that states reason, risk,
  controls, and a removal condition.

## Removal condition

This decision can be superseded after all frontend source files comply with the 400-line baseline and
the remaining delivery architecture is intentionally chosen rather than inherited.
