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
- Keep system-configuration primary disclosures single-open, retain their summary text, and keep the open heading reachable with a sticky title plus a visible collapse-current shortcut for long content. Put low-frequency forms, audits, load, and prerequisite details behind secondary disclosures without changing their DOM IDs or business actions.
- Keep Seed onboarding driven by server-computed readiness. Reuse the canonical system-configuration forms, preserve the session-only defer action, and show the exact “系统已具备运行任务条件” result only when every required readiness step passes.
- Keep onboarding and running-overview typography on the shared 17px section-title, 14px body-emphasis and 12px supporting-text scale. Present the running overview as compact role-readiness cards for Seed controller, execution, test and preproduction; evaluate only each role's common mandatory environment there and leave project-specific capabilities to task preflight.
- Keep managed platform parameters dense and aligned: three columns on wide screens, two on intermediate widths and one on narrow screens. Keep related input and select heights identical.
- Present model services as repeatable cards, not fixed provider fieldsets. Each card owns provider/authentication information plus per-role primary or ordered-backup assignments. ChatGPT device authorization must display the backend-issued code and must never expose the stored token.
- Keep section refresh actions beside their captions as compact labeled secondary buttons; do not float isolated icon-only refresh controls in long disclosures.
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
