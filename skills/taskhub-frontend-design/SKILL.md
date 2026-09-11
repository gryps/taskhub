---
name: taskhub-frontend-design
description: Maintain, refine, verify, and safely deploy the TaskHub V2 control-plane frontend. Use for TaskHub UI design, layout or responsive changes; task-center tables; workflow and system-configuration visual consistency; accessibility fixes; static HTML/CSS/JavaScript changes; frontend design-document updates; or releases to the TaskHub preproduction controller.
---

# TaskHub Frontend Design

## Establish Context

1. Work only from `/Users/gryps/taskhub-v2`.
2. Read `AGENT.md`, `docs/agent/project-context.md`, `docs/agent/memory-notes.md`, and `docs/frontend-design.md` completely before inspecting frontend source.
3. Treat `docs/frontend-design.md` as the design authority and `docs/agent/memory-notes.md` as the current implementation and deployment record.
4. Never modify a project managed by TaskHub. Restrict changes to TaskHub itself, its tests, documentation, configuration, or deployment.

## Inspect the Small Frontend Surface

Read only the relevant files under `src/taskhub_v2/api/static/`:

- `index.html` for semantic structure and stable DOM IDs.
- `styles.css` for tokens, layout, component states, and responsive behavior.
- `app.js` for authentication, projects, workflow details, and evidence.
- `task-center.js` for task-table behavior.
- `resource-center.js` for runtime-resource views.

Read the matching tests under `tests/` before changing a DOM contract. Do not introduce a build tool or framework for a local presentation fix.

## Apply the Design Contract

- Preserve API routes, SSE behavior, DOM IDs, and existing business actions unless the task explicitly changes their contract.
- Keep the three primary pages on the shared page-header and spacing system.
- Keep sidebar navigation independent from primary and secondary content-button styles.
- Reuse design tokens and existing component classes before creating selectors.
- Express status with text and color. Preserve keyboard focus, labels, `aria-*` state, and status announcements.
- Keep touch input text at least 16px without disabling browser zoom.
- Follow the current task-table truncation, inline expansion, column-width, and no-wrap rules in `docs/frontend-design.md`.
- Keep execution history inside the shared evidence typography; let the timeline alter layout only.
- Keep the development requirement textarea on its own full-width row. Put run status and the single start action in a separate footer row with the action right-aligned on desktop and full-width on phones.
- Keep one project repository card below the workflow project context. It is both the presentation and configuration surface for repository provider, Seed runtime authentication mode, remote name/URL, base branch and controller checkout. Never accept or display credentials inside a Git URL; validate the local repository, base branch and remote branch on save/test, restore the previous remote on a failed save, and block Git-mode runs before creation when repository preflight fails.
- Keep one current-project selector in the workflow page header and make both new runs and project-scoped preproduction acceptance follow it. Do not repeat a visible project selector inside the acceptance card. Keep preproduction acceptance disabled by default; require only its access URL when enabled, and place optional gateway/origin variables under advanced configuration.
- Keep system-configuration primary disclosures single-open, retain their summary text, and keep the open heading reachable with a sticky title plus a visible collapse-current shortcut for long content. Put low-frequency forms, audits, load, and prerequisite details behind secondary disclosures without changing their DOM IDs or business actions.
- Treat system-configuration primary summaries as navigation rows: show a stable order, title, short responsibility, health pill and bounded expand control, with a clear accent on the open row. Render secondary disclosures as fully bordered rounded function cards, visually distinct from primary rows and content cards.
- Keep system-configuration card typography on one hierarchy: 15px primary disclosure title, 14px card title, 13px fact value, and 12px description, label, metadata and monospace value. Do not introduce isolated 10–11px text for ordinary readable content.
- Keep Seed onboarding driven by server-computed readiness. Reuse the canonical system-configuration forms, preserve the session-only defer action, and show the exact “系统已具备运行任务条件” result only when every required readiness step passes.
- Keep onboarding and running-overview typography on the shared 17px section-title, 14px body-emphasis and 12px supporting-text scale. Present the running overview as compact role-readiness cards for Seed controller, execution, test and preproduction; evaluate only each role's common mandatory environment there and leave project-specific capabilities to task preflight.
- Keep managed platform parameters dense and aligned inside two-column business-topic cards on wide screens and one-column cards on narrower screens. Keep related input and select heights identical.
- Present physical hosts, work nodes, node containers and platform status as compact object cards matching the running-overview visual language. Put identity and state in the card header, operational facts in the body and object actions in the footer. Group host, node and platform configuration fields into business-topic cards while keeping low-frequency migration, diagnostics, backup and audit surfaces disclosed on demand.
- Present model services only as repeatable cards, without a duplicate provider resource list or fixed provider fieldsets. Each card is both the status presentation and configuration surface, and owns provider/authentication information plus per-role primary or ordered-backup assignments. ChatGPT device authorization must display the backend-issued code and must never expose the stored token.
- Keep section refresh actions beside their captions as compact labeled secondary buttons; do not float isolated icon-only refresh controls in long disclosures.
- Keep public Seed/Node image references out of the login interface, but retain the compact download disclosure in authenticated system configuration. Publish the same references in versioned deployment documentation and release pages.
- Keep username/password login on the compact login card and show the authenticated actor and role in the global header. Put user/RBAC/session security in a secondary platform disclosure; hide unauthorized mutation forms while preserving server-side authorization as the source of truth.
- In the work-node inventory, show per-node credential version/status and reconciliation time without exposing credential material. Require confirmation before credential rotation or revocation, explain the resulting target-node restart/stop, and preserve the node data volume.
- Present remote-node creation and image upgrades as durable staged operations. Use the work-node secondary upgrade card to select a node and explicit versioned image; show pull/transfer/verification/replacement/health/rollback progress in the node card, resume polling after refresh, and distinguish successful upgrade, automatic rollback and rollback failure in text. Never use a prompt dialog as the upgrade form.
- Keep host drain, maintenance, disable/re-enable and batch rebuild controls in the physical-host inventory. Remove non-active hosts from new scheduling immediately, confirm destructive transitions, and state that cross-host rebuilds retain but do not copy the source volume.
- Put per-node Agent/container/SSH-Docker logs, last error, resources and slot occupancy in one secondary diagnostics disclosure. Diagnostic ZIP exports must redact credentials, secret fields and addresses and must never contain backups or environment files.
- Keep Seed backup/recovery guidance under platform settings. Formal scripts own PostgreSQL/data-volume/config/image recovery and must validate the encryption-key/database identity before and after restore; never offer sensitive business backups as browser downloads.
- Increment the query-string version for every changed static asset referenced by `index.html`.
- Update `docs/frontend-design.md` when a design decision changes and update `docs/agent/memory-notes.md` after a release.

## Verify

Run, at minimum:

```bash
node --check src/taskhub_v2/api/static/app.js
node --check src/taskhub_v2/api/static/task-center.js
node --check src/taskhub_v2/api/static/resource-center.js
git diff --check
.venv/bin/python -m pytest tests/test_task_center.py -q
```

For structural or responsive changes, inspect 1440px and 680px browser views. Confirm that menu/content regions do not overlap and that horizontal overflow stays inside table/resource containers.

## Deploy Safely

Deploy only when the user authorizes it. Before deployment, read the current target from `docs/agent/memory-notes.md` and verify the remote hostname, application path, Git state, service, port, and health endpoint.

1. Create a timestamped backup below the remote TaskHub state `backups/` directory.
2. Synchronize only the intended files; never overwrite `.env`, `.venv`, databases, workspaces, artifacts, provider secrets, or node configuration.
3. Run `git diff --check`, restart the documented user service, and wait for health recovery.
4. Verify the health endpoint and changed asset versions over the LAN address.
5. Roll back from the new backup if health does not recover.
6. Report changed files, tests, runtime risk, deployment target, backup path, and whether worktrees remain uncommitted.

For standard product releases, use `deploy/release` rather than the rapid-development Seed Compose. Keep `.env`, backups and generated image tar files outside Git. Build one offline bundle per Linux CPU architecture, verify its `SHA256SUMS`, and run upgrade only after the scripted PostgreSQL/data-volume/image recovery point succeeds. Linux installers must require direct Docker access and must never collect sudo credentials.
