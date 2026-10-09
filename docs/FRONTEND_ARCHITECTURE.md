# TaskHub V2 Frontend Architecture

## Current surfaces

TaskHub uses one server-delivered operational frontend:

| Surface | Technology | Ownership |
| --- | --- | --- |
| Task center, development flow, system configuration | FastAPI-served HTML/CSS/JavaScript in `src/taskhub_v2/api/static/` | Native console shell and operational workflows |

FastAPI serves the console under same-origin authentication and CSRF protection. The retired React
production-canvas application is not part of the build or runtime. A future framework migration requires
a new product decision and demonstrated workflow and testing benefit. The browser never owns workflow,
permission, scheduling, or governance truth.

## Native console boundaries

- `index.html` owns semantic shell structure and stable DOM identifiers.
- Feature scripts own rendering and interaction for one operational capability.
- `api-client.js` owns cookie lookup, CSRF attachment, JSON/error normalization, and the shared request
  contract. Feature scripts consume `window.taskhubApi.request`; unauthorized responses emit one
  `taskhub:unauthorized` event for the shell to render. Event-stream behavior remains with the run shell.
- `image-downloads.js` and `image-downloads.css` own public image-registry rendering, responsive
  presentation, and clipboard interaction; the application shell does not own registry details.
- `project-activation.js` and `project-activation.css` own the project-level activation read model,
  repair navigation and draft-contract quality editor. They consume the server activation report;
  they do not recalculate project readiness in the browser.
- `external-windows-nodes.js` and its scoped stylesheet own native Windows admission. The feature
  renders server health facts and keeps the one-use SSH private key out of browser storage.
- `local-node-management.js` owns Seed-local node cards, diagnostics and lifecycle actions. Image
  drift and upgrade eligibility come from the server; the client only confirms and renders the
  health-checked result or rollback error.
- `styles.css` currently owns the incumbent token and component system. New styles must use semantic
  tokens and coherent component boundaries; the file is a registered no-growth migration target.
- Page-level code assembles features. Data transformation and server calls belong in feature modules
  or shared adapters, not in click handlers.

## Data and state flow

```text
user action -> page/feature controller -> same-origin API client -> FastAPI service
     ^                                                       |
     +----- rendered server facts / status / errors <--------+
```

- Authentication and authorization are server-enforced on every request.
- Local UI state covers disclosure, focus, selection, and unsaved draft interaction.
- First-run flow has two visible phases: Seed infrastructure onboarding may transition to the
  selected project's activation panel, while each project retains independent activation state.
- Query/cache state covers server resources and invalidation.
- Workflow, amount, resource eligibility, policy, and permission calculations remain server-owned.
- SSE invalidates or refreshes relevant server views; it does not become an alternate data authority.

## Testing and delivery

- Native changes: JavaScript syntax checks, focused Python/browser contract tests, and cache-version
  updates for changed static resources.
- Geometry or interaction changes: real Chromium at about 1440, 768, and 390px, including keyboard,
  zoom, overflow, loading, empty, error, permission, stale, and long-content states as applicable.
- The formal Docker build packages the same versioned static assets verified locally.

## Migration constraints

Historical frontend files exceed the current 400-line target. `tests/test_architecture.py` records
their current line counts as no-growth ceilings. Each touched batch must extract a complete feature,
adapter, component, or token section and lower the relevant ceiling. Arbitrary fragment files and two
parallel implementations of the same business rule are prohibited.
