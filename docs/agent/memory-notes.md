# TaskHub V2 Memory Notes

Updated: 2026-09-10

## Current Frontend Baseline

- The redesigned frontend is deployed to `192.168.31.51:8200`.
- The deployed static references are `styles.css?v=12` and `task-center.js?v=12`; unchanged scripts still use their existing versions.
- The current frontend was developed from commit `ea7389dea81f5a59dc5d193fede2a272175319b1`. The repository commit containing this memory file is the durable frontend/design baseline; a deployed checkout may still represent it as working-tree differences until its release checkout is advanced.
- The frontend remains native HTML/CSS/JavaScript served by FastAPI. No external fonts, icons, runtime packages, or build chain were added.
- The version-controlled skill is `skills/taskhub-frontend-design/`; `/Users/gryps/.codex/skills/taskhub-frontend-design/` is its installed auto-discovered copy.

## Decisions That Must Survive

- Product direction: modern project-management clarity plus enterprise-console information density.
- Primary pages share one page-header geometry and spacing system.
- Sidebar menu buttons use only navigation styles. Never add `secondary` to `nav-tasks`, `nav-workflow`, or `nav-resources`.
- Mobile/touch `input`, `textarea`, and `select` controls use at least 16px text to prevent focus zoom; browser zoom remains enabled.
- Execution history inherits the shared evidence font size, line height, padding, and text hierarchy.
- Task-center task text truncates after 15 Unicode characters; pending/blocking text truncates after 20. The inline expand/collapse button must not open the task row.
- Task-center table minimum width is 1120px. Project and current-stage cells stay on one line. The pending/blocking column uses a 175px base width and horizontal overflow stays inside the table container.
- Every changed static asset gets a bumped query-string version in `index.html` to avoid stale browser caches.

## Latest Verification

- `node --check` passed for changed JavaScript.
- `git diff --check` passed.
- `tests/test_task_center.py`: 11 passed.
- Earlier complete suite after the initial redesign: 159 passed, 6 skipped.
- Preproduction service `taskhub-v2.service` was active and `/api/health` returned `{"status":"ok","orchestrator":"langgraph"}` after the v12 deployment.

## Rollback and Operational Notes

- Preproduction backups are stored below `/home/gryps/.local/state/taskhub-v2/backups/`.
- Latest task-table backup before v12: `task-inline-expand-pre-20260910T0010/taskhub-task-inline-expand.tar.gz`.
- Deploy only intended frontend and documentation files. Preserve `.env`, `.venv`, database state, workspaces, artifacts and node/provider configuration.
- Startup currently emits LangGraph warnings about future strict msgpack handling for registered domain model types. This warning predates and is unrelated to the frontend changes.
