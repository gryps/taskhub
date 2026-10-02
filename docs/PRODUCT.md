# TaskHub V2 Product Definition

## Product goal

TaskHub V2 turns a software requirement into a persistent, productized delivery process: it creates a
versioned specification and project contract, compiles dependency-aware tasks and batches, assigns
eligible execution resources, supervises implementation and acceptance, and retains auditable code,
model, test, artifact, and Git evidence.

## Primary users

- Project owners define requirements, approve material product decisions, and inspect delivery risk.
- Developers and operators supervise runs, resolve infrastructure or contract blockers, and maintain
  execution capacity.
- Auditors inspect immutable evidence without receiving mutation privileges.
- Platform administrators configure models, nodes, security, backups, and global engineering policy.

## Primary workflows

1. Configure the Seed, models, repositories, nodes, and global engineering policy.
2. Create or connect a project and pass repository/project preflight.
3. Submit a requirement, review the product specification, and freeze a project contract.
4. Generate a dependency-aware plan; execute independent batches in parallel where contracts allow.
5. Run batch governance, tests, acceptance, review, risk, and supervision.
6. Publish only verified output and retain evidence, recovery state, and revision history.

## Success criteria

- A routine delivery can finish without a maintainer manually editing the managed project.
- Every completed batch is attributable, reproducible, and compliant with its frozen contract.
- Blockers identify the owning boundary and a recoverable next action.
- Operators can understand current project, run, task, resource, and governance state from the web UI.
- A controller restart does not lose authoritative workflow state or repeat completed node work.

## Non-goals

- TaskHub is not a general-purpose source editor or an ungoverned remote-shell frontend.
- It does not silently borrow infrastructure, credentials, or test nodes from another project.
- The browser does not become a second workflow engine or recalculate authoritative business facts.
- Platform maintainers do not bypass failed delivery runs by changing managed-project source directly.

## Acceptance baseline

The complete product must preserve authentication and authorization, project setup and preflight,
requirement-to-plan flow, batch execution, evidence and recovery, production topology, governance
visibility, responsive operation, and documented release/rollback behavior. Detailed phase requirements
remain under `docs/requirements/` and are authoritative for their respective capabilities.
