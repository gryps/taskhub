# ADR 0005: Repository-aware contract and task paths

## Status

Accepted on 2026-10-10.

## Context

Built-in project profiles used conventional paths such as `src/ui/**`, `frontend/src/**`, and
`backend/src/**`. Contract creation persisted those paths unchanged, even when an attached repository
used a monorepo layout such as `apps/web/src`, `apps/api/app`, and `apps/executor/src`. DAG compilation
then assigned implementation steps to modules by their list position. A frontend task could therefore
receive an API or nonexistent path, while a documentation task could receive a source-code path.

The invalid path contract was especially dangerous because the coding agent either had to refuse the
task or create a second, incorrect architecture at the repository root. Generated artifact requirements
were also attached to coding tasks even though those paths were forbidden from source control.

## Decision

1. Built-in profiles remain fallback conventions for empty or not-yet-structured repositories.
2. New contracts for existing repositories scan package manifests and concrete `src` or `app` roots.
   Detected roots become frozen module paths; a present `docs` directory becomes an explicit
   documentation boundary.
3. Mixed nested Node and Python packages are classified as a full-stack repository.
4. DAG compilation selects module paths from task intent and the frozen module names/paths. It never
   rotates tasks across modules solely by step number.
5. Documentation work is constrained to the documentation module. Review, audit, and final verification
   steps receive no write scope.
6. Required generated artifacts are attached to verification tasks, not implementation tasks.

## Alternatives considered

- Requiring every operator to manually rewrite inferred contracts was rejected because the default
  workflow would remain unsafe and monorepo support would depend on expert intervention.
- Letting the coding model ignore or reinterpret `allowed_paths` was rejected because path boundaries
  are an authorization contract, not advisory prompt text.
- Granting every task all repository paths was rejected because it removes useful isolation and makes
  parallel execution conflicts harder to detect.

## Consequences

- Existing active contracts remain immutable; affected projects need a newly inferred contract version.
- Repository scanning is conservative. When no concrete source roots exist, profile defaults remain in
  effect and can still be edited during contract review.
- Task-to-module intent matching is deterministic and covered by regression tests. Unrecognized
  cross-cutting implementation work receives the union of contract module paths rather than an arbitrary
  module selected by position.
