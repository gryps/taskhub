# TaskHub V2 Maintenance Boundary

## Non-Negotiable Principle

TaskHub V2 maintainers and assisting agents may modify TaskHub V2 itself, its
deployment, node environments, configuration, diagnostics, and tests. They must
not directly implement, repair, format, or commit code in projects managed by
TaskHub V2.

All managed-project source changes must be produced by a TaskHub V2 run through
its coding worker, verified by its configured gates, and retained with run,
model, test, artifact, and Git evidence. When a managed-project run blocks,
maintainers improve or repair the platform and then retry or start a clean run.
They do not bypass the platform by completing the managed-project work manually.

Reading a managed project and collecting non-mutating diagnostics is permitted.
Direct managed-project modification by TaskHub maintainers or assisting agents is
not permitted.

## Operational Self-Sufficiency

TaskHub V2 must not depend on a TaskHub maintainer or an external coding assistant
for routine project delivery. Operators provide hardware, network access, and
credentials through documented admission/configuration flows. The platform owns
node bootstrap checks, candidate deployment, database preparation, acceptance
execution, evidence collection, cleanup, and recovery.

Project-specific automation belongs in a versioned TaskHub contract generated and
maintained by the managed project's coding runs. Missing automation is returned to
the coding worker with an exact contract diagnostic; it is never converted into a
request for the operator to write commands, edit project code, or manufacture
evidence. Unsatisfied infrastructure prerequisites must fail before a run starts,
not appear for the first time during supervision.
