# TaskHub V2 Agent Context

## Project Boundary

- Git root: `/Users/gryps/taskhub-v2`
- Product: LangGraph-native AI software-delivery control plane.
- Maintainers may change TaskHub, its deployment, nodes, configuration, diagnostics, and tests.
- Maintainers must never directly change a project managed by TaskHub; managed-project changes belong to recorded TaskHub coding runs.

Always read `AGENT.md` for the complete authority boundary.

## Frontend Context

The current frontend is deliberately build-free and served by FastAPI from `src/taskhub_v2/api/static/`:

| File | Responsibility |
| --- | --- |
| `index.html` | Semantic page structure and stable DOM IDs |
| `styles.css` | Design tokens, layout, components, states, responsiveness |
| `app.js` | Authentication, project creation, workflow detail, evidence and actions |
| `task-center.js` | Task listing, filters, expansion and page navigation |
| `resource-center.js` | System, model, node and preproduction-resource views |

`docs/frontend-design.md` is the design authority. Preserve existing API, SSE and DOM contracts during visual changes unless the task explicitly changes them.

The version-controlled skill source is `skills/taskhub-frontend-design/`. Its installed, auto-discovered copy is `/Users/gryps/.codex/skills/taskhub-frontend-design/`. Keep both copies identical when the workflow changes.

## Verification

Run JavaScript syntax checks, `git diff --check`, and `tests/test_task_center.py` for every frontend change. Run the complete test suite for broad structural changes. Inspect 1440px and 680px browser layouts when page geometry or responsive behavior changes.

## Deployment Context

The TaskHub V2 preproduction controller currently runs at `192.168.31.51`:

- Application: `/home/gryps/apps/taskhub-v2`
- User service: `taskhub-v2.service`
- HTTP port: `8200`
- Health endpoint: `http://127.0.0.1:8200/api/health`
- LAN URL: `http://192.168.31.51:8200`

Do not assume another host is interchangeable. In particular, `192.168.31.55` was observed serving a separate ecommerce edge application on ports 80/443 and must not be overwritten as a TaskHub deployment target.

Remote secrets, `.env`, virtual environments, PostgreSQL data, workspaces, artifacts, provider state, and node configuration are runtime-owned and must not be replaced by frontend deployments.
