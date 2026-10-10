# TaskHub V2 Agent Context

## Project Boundary

- Git root: `/Users/gryps/taskhub-v2`
- Product: LangGraph-native AI software-delivery control plane.
- Maintainers may change TaskHub, its deployment, nodes, configuration, diagnostics, and tests.
- Maintainers must never directly change a project managed by TaskHub; managed-project changes belong to recorded TaskHub coding runs.

Always read `AGENTS.md` for the complete authority boundary. `AGENT.md` is retained only as a
compatibility pointer for older tooling. The standard product and architecture context is in
`docs/PRODUCT.md`, `docs/ARCHITECTURE.md`, `docs/MODULES.md`, `docs/DESIGN.md`, and
`docs/FRONTEND_ARCHITECTURE.md`.

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

Run JavaScript syntax checks, `git diff --check`, and the focused frontend contract tests for every
frontend change. Run the complete test suite for broad structural changes. Inspect 1440px, 768px,
and 390px browser layouts when page geometry or responsive behavior changes; retain the historical
680px compatibility check where the existing browser suite requires it.

## Deployment Context

The current formal environment is the local Docker deployment on this Mac. There is no separate
production host at present:

- Formal Seed URL: `https://127.0.0.1:8200`
- Connection descriptor: `/Users/gryps/.codex/taskhub-v2-connection.json`
- Windows acceptance host: `user@192.168.31.34`
- Windows acceptance node ID: `windows-test-01`

The Windows machine is a project-authorized test resource, not a Seed host, coding node, physical-host
pool entry, or deployment target. Project contracts bind its logical node ID; TaskHub product code
must not hard-code its IP address or SSH identity.

Earlier `.31`, `.21`, and `.51` deployments remain historical evidence only. They are not current
readiness prerequisites and must not delay local-formal deployment or `.34` Windows acceptance.
In particular, `192.168.31.55` was observed serving a separate ecommerce edge application on ports
80/443 and must not be overwritten as a TaskHub deployment target.

Remote secrets, `.env`, virtual environments, PostgreSQL data, workspaces, artifacts, provider state, and node configuration are runtime-owned and must not be replaced by frontend deployments.
