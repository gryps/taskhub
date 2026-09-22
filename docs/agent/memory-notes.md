# TaskHub V2 Memory Notes

Updated: 2026-09-22

## Phase 7 Single-Seed Backend and Quality Baseline

- Active TaskHub applications no longer mount the physical-host or remote-node APIs and no longer
  start SSH host monitoring or remote-node reconciliation. Historical stores and implementation
  modules remain untouched for upgrade audit and rollback compatibility.
- Production readiness now verifies Seed-local Docker node management instead of requiring a
  remote-node service. Diagnostics remain backward compatible but emit empty legacy host sections
  and collect active local containers, scheduler state and operations.
- Repository-wide Ruff was restored from 28 findings to a clean result. The Makefile automatically
  uses `.venv/bin/python` when available and exposes a combined Python/frontend quality target.
- The React production canvas now has Vitest coverage for typed-edge inference and preservation of
  server-owned capability and priority facts during client-side save transformations.
- GitHub CI covers Python 3.12–3.14 with PostgreSQL, frontend unit/build checks, and opt-in browser
  acceptance with a real PostgreSQL service and Playwright Chromium.

## Production Canvas Formal Registry Release

- On 2026-09-13, source commit `1cc5185` was formally released as `0.1.0-alpha` after correcting
  canvas runtime-package deployment and cache behavior. Verification passed with the Vite/TypeScript
  build, JavaScript syntax checks, `271 passed, 19 skipped`, and real Chrome canvas checks at 1440,
  680 and 390 pixels.
- Docker Desktop's SSH session still cannot access its GUI credential helper for public base-image
  metadata. Because `pyproject.toml`, `taskhub-web/package.json` and the lock file are unchanged from
  the verified Phase 0–6 environment, the release rebuilt the complete TaskHub application package
  and React assets on those complete Seed/Node environments, compiled both Python package trees,
  and recorded exact revision `1cc5185b5e210478a7f8c0bc8fac10c13bdfb9c2`.
- The released `linux/amd64` Seed digest is
  `sha256:13a00347fea5bb786bcaa3cb6cd8a47dbb488231eda6f84c7902fed46209488a`; the Node digest is
  `sha256:0ff618dd695017d2db5a1e965afa517877e670043a522e255bbd8671ab2d70e9`.
  GHCR and Aliyun ACR expose byte-identical digests for both images, and anonymous registry API
  reads return HTTP 200 for all four public references.
- Seed and Node candidate containers both reached healthy. The Node candidate retained Codex CLI
  0.153.4, Git and Node.js. The controller at `192.168.31.31:8200` now runs the released Seed,
  remains healthy with zero observed restarts, retains configured password login and all 14 public
  PostgreSQL tables, and serves `index-DFtnAf4z.js` from the actual installed package path.
- The validated pre-release recovery set is
  `C:\taskhub-seed\deploy\release\backups\20260912T175254Z`. Registry credentials existed only in
  a temporary Docker CLI directory on `.31`; the credential directory, build context, source
  archive and helper scripts were removed after publication.

## Production Canvas Runtime-Package Correction

- On 2026-09-13, live request logs proved that the two preceding frontend-only overlay images had
  changed `/opt/taskhub/src/.../canvas` but not the installed package actually resolved by
  `importlib.resources`. The browser therefore continued receiving old bundle
  `index-CWwwJ0sY.js`, issued no draft-creation request, and could not show the node in either view.
- Source commit `1cc5185` adds an explicit no-store `/canvas/` HTML route. The corrected overlay
  updates both source and `/usr/local/lib/python3.12/site-packages/taskhub_v2/api` runtime paths.
  The installed HTML and JS SHA-256 values now exactly match the locally verified Vite build, which
  serves `index-DFtnAf4z.js`.
- The running Seed at `192.168.31.31:8200` is
  `sha256:f91f0c82da352882d58b33c17ff82d78f8b4747edcd5c087719c29908271eb21`.
  The controller and PostgreSQL are healthy with zero observed controller restarts. The pre-update
  backup is `C:\taskhub-seed\deploy\release\backups\20260912T175254Z`, and the previous image is
  tagged `taskhub-seed:rollback-pre-canvas-runtime-path-20260913`. No registry image was pushed.

## Production Canvas Empty-State Context Action Follow-up

- On 2026-09-12, the first context-position fix was followed by the missing live empty-state fix:
  `.31` had no stored production topology, while the context menu appeared actionable but the
  client still required a draft. A context-menu add from an empty, active or other read-only view
  now creates or selects the draft and appends the requested node as one operation.
- The revised real-Chrome test begins with no topology and no prior **New version** action, then
  right-clicks the canvas and verifies the draft, three visible nodes and the new card at the click
  point at 1440, 680 and 390 pixels. The React build, 15 targeted tests and three browser cases pass.
- Source commit `e9b5c65` is deployed at `192.168.31.31:8200`. The running Seed image is
  `sha256:1df3103691a04f0f53072b139d6eb6ae3beb6f73cb74fc1a7d840e88bab7a625` and serves
  `index-DFtnAf4z.js`; the controller and PostgreSQL are healthy with zero observed controller
  restarts. The pre-update backup is
  `C:\taskhub-seed\deploy\release\backups\20260912T155203Z`, and the prior image remains tagged
  `taskhub-seed:rollback-pre-canvas-autodraft-20260912`. No registry image was pushed.

## Production Canvas Context-Menu Fix Deployment

- On 2026-09-12, production-canvas context-menu node creation was fixed and deployed to the
  existing Windows Docker Desktop Seed at `192.168.31.31:8200` from source commit `7fa6101`.
  React Flow now converts the right-click screen location through the current viewport before
  creating the node, so panning or zooming cannot place the new card outside the visible area.
  Context-menu mutations also enforce the same draft-only rule as the toolbar.
- Verification passed with the React/TypeScript/Vite production build, 15 targeted topology/static
  tests and real Google Chrome at 1440, 680 and 390 pixels. The browser test asserts both node count
  and the rendered card's proximity to the right-click point.
- The running Seed image is `sha256:2dc8a0e769125647688047e4efba66b2099409d349feb864fafa51e3d1296109`
  and serves canvas bundle `index-B1F07eMi.js`. The controller and PostgreSQL are healthy, the
  controller has zero observed restarts, and the LAN health endpoint passes.
- The complete pre-update backup is
  `C:\taskhub-seed\deploy\release\backups\20260912T154039Z`; the previous image remains tagged
  `taskhub-seed:rollback-pre-canvas-context-20260912`. Existing configuration, administrator state,
  PostgreSQL and named volumes were retained. No registry image was pushed.

## Productized Delivery Phase 0–6 Deployment

- On 2026-09-12, the completed Phase 0–6 source was deployed to the existing Windows Docker
  Desktop Seed at `192.168.31.31:8200`. The running Seed image is
  `sha256:ce663471607c95b1dbf19caeb3fe60ef001d1298ca5f7b065e179a9f9bebd803`; the local unified
  Node image is `sha256:6c98f55c41c5329917be45216aa96510b92a0242e434c5a7ff4a84581215c24a`.
  Both carry source revision `43a6ef8a47720c01717b8b1ec8bd23b2f249c173`.
- Docker Desktop's credential helper could not be used from its SSH logon session, so public base
  image pulls failed before changing the runtime. Because Phase 0–6 added no image dependency, the
  release used the previously complete Seed/Node images as verified bases, replaced only TaskHub
  source plus the locally built React canvas, and recompiled the Node package. Both candidate
  containers passed their own health checks before the controller was recreated.
- The deployment retained PostgreSQL and named volumes `taskhub-seed_postgres-data` and
  `taskhub-seed_taskhub-data`, including the configured administrator password. PostgreSQL exposes
  14 expected public tables after migration. `TASKHUB_PRODUCTION_ORCHESTRATION_ENABLED=true` is now
  explicit in the live Compose, so the Phase 0–6 UI/API is active rather than merely installed.
- The live controller is healthy with zero restart failures and serves `styles.css?v=46`,
  `app.js?v=19`, the capability center and the protected React canvas. The pre-release backup is
  `C:\taskhub-seed\deploy\release\backups\20260912T040818Z`; all recorded SHA-256 checks pass and
  its PostgreSQL dump contains `taskhub_backup_identity`.
- Immediate rollback tags are `taskhub-seed:rollback-pre-phase6-20260912` and
  `taskhub-node:rollback-pre-phase6-20260912`; the previous Compose is retained as
  `compose.pre-phase6.yaml`. No registry image was pushed during this deployment.
- This existing `.31` stack remains the trusted-LAN HTTP layout with a direct Docker Desktop socket
  mount. Migrating it to the formal TLS + restricted Socket Proxy topology is a separate operator
  change because it changes the access URL and certificate trust behavior.

## Productized Delivery Phase 6

- On 2026-09-12, Phase 6 completed the source implementation for project concurrency, weighted
  priority, per-run cost budgets, grouped 1,000-task readiness checks, critical-path and bottleneck
  analysis, and paginated execution-plan text views.
- Project policies are persisted and frozen into new execution plans. The global scheduler supports
  20 concurrent grants, applies weighted fairness without starving lower-priority projects, and
  blocks over-budget tasks before dispatch with an explicit waiting reason.
- Development Workflow now presents a compact project policy card plus server-owned prediction,
  cost, quality and bottleneck facts. Tasks are fetched 100 at a time. Static assets are
  `styles.css?v=46` and `app.js?v=19`.
- `/api/system/production-readiness` reports persistent orchestration, secure sessions, RBAC, audit,
  backup identity, Docker proxy isolation and node reconciliation controls without exposing secrets.
- The production canvas remains the only React route with editable topology state. Other console
  routes remain native until whole-route capability parity; no scheduling truth is duplicated in
  either frontend.
- This phase is source-only: push `.3 Git` and GitHub. Do not deploy, build Docker images or publish
  registries. A real target-environment release/restore drill remains a deployment gate rather than
  a fabricated source-stage result.
- Verification passed with `271 passed, 19 skipped`, the React/TypeScript/Vite production build,
  changed-file Ruff, JavaScript syntax, architecture and diff checks. Real Google Chrome passed the
  policy/analysis/task layouts at 1440, 680 and 390 pixels without page-level horizontal overflow.

## Productized Delivery Phase 0 Baseline

- Productized delivery development is now tracked by
  `docs/requirements/productized-delivery-orchestration.md` and the durable phase ledger
  `docs/agent/productization-progress.md`.
- Phase 0 adds ProductSpec, ExecutionPlan, ProductionTask, TaskAttempt, ChangeRequest and
  CapabilityPack schemas, explicit state-transition invariants, immutable approved/active version
  content and content digests. Draft content remains editable for the following phases.
- Memory and PostgreSQL stores share the same create/get/list contract. PostgreSQL uses additive,
  idempotent `taskhub_schema_migration` and `taskhub_production_object` tables and does not alter
  LangGraph checkpoints or the existing task index.
- Existing runs can be projected as a Legacy Linear Plan with one approved specification, one
  plan, one task and one dynamic compatibility batch. This projection deliberately does not invent
  detailed DAG or acceptance evidence.
- `TASKHUB_PRODUCTION_ORCHESTRATION_ENABLED` defaults to `false`; disabling it is the application
  rollback path and the additive tables remain intact. Phase 1 is the next implementation boundary.
- This phase is source-only: push to `.3 Git` and GitHub, with no deployment, Docker build or
  registry upload.

## Workflow Project Context and Local Image Build

- Feature commit `601cc11b4fca4ad60168f0dd23fd0287e9305a31` makes the workflow header the single current-project selector; new runs and project-scoped preproduction acceptance follow that shared context.
- The former project-settings row is now the 15px `预生产验收` disclosure. It is disabled by default, requires only the preproduction access URL when enabled, derives a missing gateway host from that URL, and keeps optional gateway/origin variables under advanced configuration for custom deployment and acceptance scripts.
- The frontend design document plus the version-controlled and installed TaskHub frontend skills retain this interaction contract. Static assets are `styles.css?v=38`, `app.js?v=12` and `resource-center.js?v=25`.
- Verification passed: 214 tests passed and 8 skipped; the real Chrome layout test passed with the system browser; JavaScript syntax, diff checks and changed-file Ruff excluding the repository's pre-existing `UP046` generic warning passed.
- Local linux/amd64 images were rebuilt on `192.168.31.31` without registry upload: Seed `taskhub-seed:0.1.0-alpha` is `sha256:acdabccce3aeeb6d3bf996cca74dc9e257956abef01b8628bc0ed0afdc4a1bcf`; Node `taskhub-node:0.1.0-alpha` is `sha256:aae5839d50e176d6e08aff3c60b164d12389e6115c7b7cb6fa8de60b96c3620a`. Both carry revision `601cc11b4fca4ad60168f0dd23fd0287e9305a31`.
- Docker Desktop's remote SSH session could not use its credential helper, and both Debian upstream and mirror downloads stalled during a full Node dependency rebuild. Because this change does not alter Node system dependencies, the successful Node build reused the previously verified Node environment image and reinstalled the committed TaskHub source before applying the new OCI revision label.
- GHCR and Aliyun ACR were not contacted for upload, and the running `.31` controller was not recreated or deployed from these local images.

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

## Seed Node Alpha Baseline

- On 2026-09-10, the seed package was deployed to Windows Docker Desktop at `192.168.31.31`; its LAN entry point is `http://192.168.31.31:8200`.
- The deployment root is `C:\taskhub-seed`. The host-local `deploy\seed\.env` contains generated credentials and must never be committed or copied into an image.
- The running stack contains `taskhub-seed-controller-1` and `taskhub-seed-postgres-1`. PostgreSQL is internal-only; port `8200/tcp` is the only published application port.
- Persistent volumes are `taskhub-seed_taskhub-data` and `taskhub-seed_postgres-data`. Both containers returned to healthy after restart with the same volume names, and the database retained six public application tables.
- Docker Hub access was unreliable from this host. The verified fallback is the DaoCloud prefix mirror `m.daocloud.io/docker.io/library/...`; the selected linux/amd64 image manifests matched the official Docker Hub manifests before deployment.
- This alpha proves a self-contained Web/API controller with LangGraph and PostgreSQL. Web-driven creation and configuration of additional role containers is the next product increment, not part of this baseline.
- The seed controller now enables persistent administrator-password login through `TASKHUB_ADMIN_PASSWORD_FILE=/var/lib/taskhub/config/admin-password.json`. Its first page requires the deployment bootstrap token plus a new password and confirmation; later logins accept only the password.
- Passwords are stored only as salted `scrypt` hashes. The deployment was deliberately left in `setup_required=true` state so the operator, not the maintainer, chooses the first password.
- The first-password release uses `styles.css?v=13` and `app.js?v=6` on `.31`. Edge screenshots at 1440x1000 and 680x900 confirmed a centered, readable setup card without page overflow.
- Rollback assets for this release are `C:\taskhub-seed\backups\auth-pre-20260910T054833` and Docker image `taskhub-v2-seed:backup-20260910T054833`.
- Release verification: 164 tests passed, 6 skipped; targeted Ruff, JavaScript syntax, Compose config, container health, LAN auth status, static asset versions, persistent volume names and six PostgreSQL application tables all passed.

## Seed Web Container Lifecycle (Rapid Development)

- On 2026-09-10, rapid-development source mounts were enabled on `192.168.31.31`; the Docker image remains `taskhub-v2-seed:0.1.0-alpha` and was not rebuilt.
- **系统配置 → 节点容器** creates execution, test and preproduction node containers, starts/stops/removes them, and keeps `nodes.json` synchronized with container lifecycle. Removing a user node preserves its named data volume by default.
- Web-created nodes join only `taskhub-seed_default` and publish no host ports. The controller has authenticated Docker Socket access, which is host-administrator-equivalent; this alpha must remain on a trusted LAN.
- The current seed image proves container lifecycle, Python node-agent health and scheduling registration. It intentionally lacks Codex, Git, SSH, Node.js and browsers; the confirmed release direction is one unified `taskhub-node` image with role-controlled startup profiles.
- Rapid verification created `smoke-test-0910`, observed it as Docker `healthy`, confirmed authenticated node health and the `test`/`acceptance` workloads, then removed both the temporary container and its data volume. The node registry returned to empty.
- Targeted verification after the change: Ruff passed; 19 auth/container/task-center tests passed; all three frontend JavaScript syntax checks and `git diff --check` passed. Edge inspection at 1440×1000 and 680×900 showed consistent system-configuration styling, stacked narrow-screen fields and no page-level horizontal overflow. LAN health remained `{"status":"ok","orchestrator":"langgraph"}`; static assets are `styles.css?v=14` and `resource-center.js?v=6`.

## Seed Managed Configuration (Rapid Deployment)

- On 2026-09-10, the second system-configuration increment was rapidly deployed to `192.168.31.31:8200` at the operator's request. Browser visual acceptance was deliberately deferred to the operator; this was not a formal Git/GitHub release.
- **系统配置 → 模型服务** now manages provider mode, API base URLs, proxy, role models and replace-only API keys. **平台设置** now manages controller/agent URLs, node image defaults, resource limits, heartbeat thresholds and retention policies.
- Managed configuration is durable in PostgreSQL tables `taskhub_managed_config` and `taskhub_config_audit`. Provider keys are encrypted with the deployment-local `TASKHUB_CONFIG_ENCRYPTION_KEY`; APIs return only configured/masked state and never plaintext.
- Saves produce desired/effective versions and a restart-required state. Connection tests, saves and startup application produce audit records without secret values.
- The rapid image contains `cryptography 46.0.7`; the existing Compose volumes, administrator password and PostgreSQL data were retained. Static assets are `styles.css?v=16` and `resource-center.js?v=8`.
- Verification: changed-file Ruff, all three frontend JavaScript syntax checks, Python compilation and `git diff --check` passed; full suite completed with 175 passed and 7 skipped. Both LAN and container health returned `{"status":"ok","orchestrator":"langgraph"}`, protected configuration API access returned HTTP 401 without a session, and both managed-configuration tables were present.
- Rollback image: `taskhub-v2-seed:backup-phase2-20260910T174243`. Because rapid development uses a host source bind, image rollback also requires disabling that bind or restoring the earlier source. Deployment-secret backup: `C:\taskhub-seed\backups\phase2-pre-20260910T174243`; it contains `.env` and must remain host-local.

## Seed SSH Host Admission (Rapid Deployment)

- On 2026-09-10, commit `898c017` was rapidly deployed to `192.168.31.31:8200`. The operator explicitly deferred pytest, browser inspection and functional SSH acceptance; only syntax, compilation, lint, secret scanning and runtime health checks were performed.
- **系统配置 → 物理主机** now provides remote Linux Docker host inventory, SSH fingerprint discovery and confirmation, encrypted private-key storage, admission checks and manual recheck. No physical host or user private key was entered by the maintainer.
- Admission requires strict host-key checking plus passwordless SSH, Docker permission, Linux hardware discovery and connectivity from the remote host to the configured Node Agent callback URL. A changed fingerprint or failed check changes the host out of the available state.
- PostgreSQL table `taskhub_physical_host` stores inventory and encrypted credentials. The API and audit return only non-secret metadata; the Seed image contains OpenSSH client tools but no private key or preconfigured host trust.
- This is stage 3A only. Remote node creation, desired-state lifecycle and the unified `taskhub-node` image remain stage 3B; the UI does not claim those capabilities are complete.
- Runtime checks: controller and PostgreSQL healthy, LAN health returned `{"status":"ok","orchestrator":"langgraph"}`, OpenSSH client was present, unauthenticated `/api/hosts` returned HTTP 401, and static assets were `styles.css?v=17` and `resource-center.js?v=9`.
- Rollback image: `taskhub-v2-seed:backup-ssh-hosts-20260910T184909`. The prior source backup remains `C:\taskhub-seed\backups\release-3d937f4-pre-20260910T180524`; rapid source-bind rollback must restore that source or disable the bind before starting the backup image.

## Seed Remote Node Orchestration (Rapid Deployment)

- On 2026-09-10, feature commit `e233d14` was rapidly deployed to `192.168.31.31:8200` and pushed to both `.3 git` and GitHub.
- Physical-host purpose is now a fixed multi-select capability set: execution, test and preproduction. Remote-node creation enforces the selected host's allowed roles.
- **系统配置 → 工作节点** can select an admitted remote host, create a role node with Agent port and CPU/memory defaults, and remotely start, stop, restart or remove it over strict-host-key SSH. Local and remote containers share one inventory view.
- Remote node inventory is durable in PostgreSQL table `taskhub_remote_node`; node, host, role, image digest, desired/actual state and resource settings survive Seed restarts. A node is added to `nodes.json` only after its authenticated Agent health check succeeds.
- Unified worker image `taskhub-node:0.1.0-alpha` was built on `.31` with image ID `sha256:b1e11130fd6ab0742f45cbfe9385c359ccfb23a037dec2a6d343093525cd31cc`. The rapid runtime build used the existing Seed image plus the Aliyun Debian mirror to avoid a second full Python dependency build; the committed release Dockerfile is `deploy/node/Dockerfile`.
- Static assets are `styles.css?v=18` and `resource-center.js?v=10`. PostgreSQL and controller are healthy, `/api/remote-nodes` is authentication-protected, and the new table exists.
- At the operator's explicit request, pytest, browser inspection, real SSH node creation and functional acceptance were skipped. Only JavaScript/Python syntax, Ruff, diff, import, health, static-version, image and table checks were performed.
- Known Alpha limits: remote Agent ports are LAN-published; all nodes still share the deployment node token; automatic periodic reconciliation and image upgrade/rollback are not implemented; the unified image includes Git and Node.js/npm but not Codex CLI or browsers.
- Rollback source: `C:\taskhub-seed\backups\stage3b-pre-20260910T200350`. Rollback image: `taskhub-v2-seed:backup-stage3b-20260910T200350`. Preserve the host-local `.env` and named volumes during rollback.

## Seed Image Distribution and Configuration Disclosure (Rapid Deployment)

- On 2026-09-10, the current rapid-development source was deployed to `192.168.31.31:8200` without rebuilding `taskhub-v2-seed:0.1.0-alpha` or `taskhub-node:0.1.0-alpha`.
- Remote-node creation now runs as a persistent background progress operation. It can try a private registry, a domestic mirror prefix, the configured image reference, the remote cache, and finally Seed-side Docker export plus strict-host-key SSH `docker load`; it validates Linux OS, CPU architecture and the available image digest/ID before container creation.
- System configuration now uses a single-open primary accordion. The open heading sticks below the global header, a fixed “收起当前项” shortcut appears for long content, and the last open primary group is remembered. Low-frequency model/host/node/platform forms, load, prerequisites and audit trails use secondary disclosures while status summaries and resource lists stay visible.
- The version-controlled and installed `taskhub-frontend-design` skill both retain this interaction contract. Deployed static assets are `styles.css?v=20` and `resource-center.js?v=12`.
- At the operator's request, pytest, browser visual inspection, real SSH image distribution and Docker image rebuild were skipped. JavaScript syntax checks and `git diff --check` passed before deployment; the controller returned healthy after restart and the LAN page served the new asset versions and collapse control.
- Rollback source: `C:\taskhub-seed\backups\config-disclosure-pre-20260910T234457`. The host-local `.env`, administrator password, PostgreSQL data and named volumes were preserved.

## Seed First-Run Onboarding (Rapid Deployment)

- On 2026-09-11, feature commit `0e7436f` was pushed to both `.3 git` and GitHub and rapidly deployed to `192.168.31.31:8200` through the existing source bind. Neither `taskhub-v2-seed:0.1.0-alpha` nor `taskhub-node:0.1.0-alpha` was rebuilt.
- Incomplete authenticated Seed deployments now open a first-run status page. Its seven server-computed conditions cover administrator setup, Docker/CPU/memory/persistent disk, effective Seed/callback URLs, a local or remote image source, effective model configuration, one admitted physical host and one healthy schedulable node.
- Docker Engine `/info` supplies Docker VM CPU, memory, OS and architecture; TaskHub's persistent filesystem supplies free-disk capacity. The wizard reuses existing system-configuration forms and shows `系统已具备运行任务条件` only after all required conditions pass.
- The wizard can stream a `docker save` tar archive of up to 20 GB into the Seed Docker Engine, verifies the expected image reference and returns the archive SHA-256, image ID/digest, OS and architecture. Temporary uploads are removed after success or failure.
- Pending model/platform settings expose an authenticated Web restart action for Seed container deployments. It exits the controller after accepting the request; Compose `unless-stopped` restores it and the page waits for health recovery.
- Static assets are `styles.css?v=21`, `app.js?v=7`, `task-center.js?v=13`, `resource-center.js?v=13` and `onboarding.js?v=1`.
- At the operator's explicit request, pytest, browser inspection, offline-image functional upload, Web-triggered restart and real SSH/node acceptance were skipped. JavaScript syntax, Python compilation, changed-file Ruff, `git diff --check`, runtime import, container health and served asset references were checked.
- Rollback source: `C:\taskhub-seed\backups\onboarding-pre-20260911T002101`. Preserve the host-local `.env`, administrator password, PostgreSQL data and named volumes during rollback.

## Role-Based Running Overview (Deployed)

- On 2026-09-11, the first-run wizard and running-overview typography were aligned to the shared 17px section-title, 14px emphasis and 12px supporting-text hierarchy.
- The running overview now uses four compact readiness cards for Seed controller, execution, test and preproduction. It distinguishes not deployed, offline, missing capability and ready states, and no longer presents controller-wide diagnostics as a sparse four-column table.
- Common role requirements are intentionally narrow: Seed checks Docker/CPU/memory/persistent disk; execution checks Git/Python/coding/workspace sandbox; test and preproduction check Git/Python/pytest. Project-specific Node.js, browser and database capabilities remain task-preflight concerns.
- Local verification used real Chrome at 1440×1000, 680×900 and 390×844. Typography, 2-column/1-column card transitions, sidebar geometry, mobile navigation, JavaScript errors and page-level horizontal overflow all passed. `tests/test_system_config_browser.py` retains this browser regression. The full automated suite was restored after correcting earlier Fake Docker `/info` coverage and the 400-line architecture limit.
- Feature commit `d5b44e6` was pushed to `.3 git` and GitHub, then deployed through the existing source bind to `192.168.31.31:8200` on 2026-09-11. Static assets are `styles.css?v=22`, `resource-center.js?v=14` and `onboarding.js?v=2`; both controller and PostgreSQL were healthy after restart and the LAN health endpoint returned `{"status":"ok","orchestrator":"langgraph"}`.
- Rollback source: `C:\taskhub-seed\backups\role-overview-pre-20260911T005500`. The deployment replaced only the four changed static files and `services/containers.py`; `.env`, administrator credentials, PostgreSQL data, named volumes, workspaces, artifacts and node configuration were preserved. No Docker image was rebuilt.

## Dynamic Model Cards and Stable Failover (Rapid Deployment)

- On 2026-09-11, the system-configuration model form was replaced by repeatable model cards. Each card owns its provider, API or ChatGPT-account authentication, address/model/proxy, enabled state and independent planner/coder/supervisor/reviewer/risk primary-or-backup assignment.
- ChatGPT account cards start Codex CLI device authorization on the Seed controller, display the backend-issued URL and device code, and poll until authentication completes. Account `auth.json` files remain below `/var/lib/taskhub/config/model-accounts`; API keys remain encrypted in managed configuration and neither credential is returned to the browser.
- Role routing now requires one primary and unique ordered backups. Persistent failover opens only after three consecutive service failures, waits at least 300 seconds, probes every 30 seconds, and restores the primary for new work only after three successful probes. Permission, parameter and content failures do not persistently trip the circuit; failover and recovery transitions are retained in a protected JSONL operational log.
- Managed platform parameters use a denser 3/2/1-column responsive grid and include the four failover thresholds. Model-card inputs share a 38px height. Primary disclosure refresh controls are compact labeled secondary buttons beside their section captions.
- Rapid source deployment completed on `192.168.31.31:8200` with `styles.css?v=23` and `resource-center.js?v=15`. Official Codex CLI 0.154.0 for Linux x86_64 was placed in the existing TaskHub data volume at `/var/lib/taskhub/config/bin/codex`; neither Seed nor worker image was rebuilt.
- The controller and PostgreSQL were healthy and LAN health returned `{"status":"ok","orchestrator":"langgraph"}` after restart. Per operator direction, pytest, browser visual inspection, live account authorization and model failover acceptance were skipped; JavaScript/Python syntax, changed-file Ruff, application construction and diff checks passed.
- Rollback source: `C:\taskhub-seed\backups\model-cards-pre-20260911T014748`. Existing `.env`, administrator credentials, PostgreSQL data, named volumes, workspaces, artifacts and node configuration were preserved.
- Follow-up refinement removed the duplicate model-resource table and the model-management secondary disclosure. Model cards are now the direct and only model-service presentation/configuration surface. It was deployed with `styles.css?v=24` and `resource-center.js?v=16`; rollback source is `C:\taskhub-seed\backups\model-card-surface-pre-20260911T015316`.

## Standard Release Delivery Kit (Published and Deployed)

- On 2026-09-11, `deploy/release` was added as the immutable product-delivery surface. It uses published `taskhub-seed`, unified `taskhub-node` and PostgreSQL images without the rapid-development source bind.
- Bash and PowerShell entry points cover image builds, online/offline initialization, architecture-specific offline bundles, checksums, upgrade backups and destructive-but-scoped recovery of only `taskhub-data` and `taskhub-postgres-data`.
- Initialization never requests sudo credentials. The Linux operator must already have direct Docker access; Windows uses Docker Desktop Linux containers and Compose v2.
- Release images pin and install the checksum-verifying official Codex installer. The Seed uses it for device authorization; the Node image also includes Git, Python/pytest, Node.js/npm and the Playwright Python package. Browser binaries remain project-specific.
- Offline bundles are generated below ignored `dist/` and contain three images, Compose, scripts, platform docs, a JSON image-ID manifest and `SHA256SUMS`. Large tar artifacts and host-local `.env`/backups are not version controlled.
- On 2026-09-11, the standard kit was published from image-source commit `333c551` and deployed to `192.168.31.31:8200`. The immutable runtime now uses `taskhub-seed:0.1.0-alpha` and `taskhub-node:0.1.0-alpha`; the controller has no source-code bind mount and retains only the TaskHub data volume plus the required Docker Socket bind.
- The `linux/amd64` offline bundle is `C:\taskhub-releases\taskhub-offline-0.1.0-alpha-amd64`. Its 660,647,424-byte image archive, Compose, lifecycle scripts, platform documentation, image manifest and 19-file `SHA256SUMS` were generated and verified on the target Docker host.
- The standard Windows backup script was exercised against the deployed stack and created `C:\taskhub-seed\deploy\release\backups\20260910T193024Z`. A broader pre-migration recovery point remains at `C:\taskhub-seed\backups\formal-release-pre-20260911T031356` with the legacy configuration, PostgreSQL dump, TaskHub data, images and source snapshot.
- Windows PowerShell 5.1 compatibility follow-up `60566c0` adds UTF-8 BOMs and prevents normal Docker Compose stderr progress from becoming a terminating error while preserving strict cmdlet failures. All six PowerShell scripts parsed successfully on `.31`; `tests/test_release_delivery.py` has a regression check for this contract.
- Static delivery verification passed: immutable Compose rendering, all Bash syntax checks, Windows PowerShell 5.1 parsing on `.31`, changed-file Ruff, secret-pattern scanning and the complete Python suite (`187 passed, 8 skipped`). Local Docker Desktop was not running, so final image builds and tar checksums remain intentionally unclaimed.

## Per-Node Credentials and Desired-State Reconciliation (Deployed)

- On 2026-09-11, managed work nodes were changed from one deployment-wide Agent token to independent high-entropy credentials. The Seed stores only encrypted credential material in its persistent configuration volume; node/API views expose version, status and a non-secret fingerprint, never the token or ciphertext.
- Creation injects a credential only into its target node. The scheduler resolves credentials by node ID with no shared-token fallback. Rotation replaces only the target container while preserving its named volume; manual revocation stops and unschedules the node, while a later start signs a new credential; successful deletion revokes the credential and removes its ciphertext.
- A background coordinator now checks each physical host once per cycle, then compares the PostgreSQL desired node state with deterministic remote container state and authenticated Agent identity/health. Missing containers are recreated against the same named volume, stopped containers follow desired state, and duplicate reconciliation cannot overlap for one node.
- Unreachable or unhealthy nodes are removed from `nodes.json` immediately and therefore stop receiving new work. After SSH/network and Agent health recover they are registered again. Reconciliation timestamps, reason and last successful Agent observation persist in the remote-node record and appear in the work-node inventory.
- The formal release Compose no longer requires `TASKHUB_NODE_TOKEN`; existing rapid-development Compose retains it only for backward-compatible local Alpha operation. Both TaskHub images carry revision `333c55121ade6ce507a7dea1ec22b73f64b6fa05` and are `linux/amd64`.
- Release verification completed with `194 passed, 8 skipped`; changed-file Ruff, frontend JavaScript syntax and diff checks passed. On `.31`, controller/LAN health, retained administrator-password state, all five static asset versions, Seed/Node toolchains and a temporary authenticated Node Agent using a fresh independent token passed. The smoke container was removed after verification.

## Public Image Download Surface (Rapid Deployment)

- Public release registry prefixes are `ghcr.io/gryps` and `crpi-kgqnka7pmz9f3sml.cn-hangzhou.personal.cr.aliyuncs.com/taskhub-v2`. Both Seed and Node repositories are public. These endpoint/namespace values are non-secret durable release configuration; registry usernames, passwords and tokens must never be written to this file, Git, images or logs.
- On 2026-09-11, the login page and authenticated system-configuration page gained a shared, compact public-image download disclosure. It shows the explicit `0.1.0-alpha` `linux/amd64` Seed/Node references for both GHCR and the Aliyun Hangzhou ACR, and copies complete `docker pull` commands.
- The two UI locations render from one `app.js` registry list and do not add a sixth primary system-configuration disclosure. Deployment documentation and the version-controlled/installed frontend skill retain the same contract.
- The rapid runtime update on `192.168.31.31:8200` serves `styles.css?v=25` and `app.js?v=8`. LAN health remained `{"status":"ok","orchestrator":"langgraph"}`; live Chrome checks at 1440, 680 and 390 pixels found four login-page image rows, no JavaScript errors and no page-level horizontal overflow.
- Rollback files are in `C:\taskhub-seed\backups\public-download-pre-20260910T211127Z`. The backup was extracted from an unmodified temporary container created from `taskhub-seed:0.1.0-alpha`, then that container was removed.
- Formal dual-registry publication followed from image-source commit `b163ab80317353da4b3b85c234ce13ce445ea134`. The release build accepts configurable Debian and security mirrors with package-download retries; the final `linux/amd64` images disable default OCI provenance attestations for Aliyun ACR compatibility.
- The fixed `0.1.0-alpha` tags were overwritten in both registries, not supplemented with extra release tags. Seed digest is `sha256:dacdd405fda707920efcab92a08295a0659422719f87667a41d3b449a2d3b868`; Node digest is `sha256:fc4f524f85970923add0c5840cb9edd306c6c83b7a62ac90a4a981127ab6f249`. Anonymous manifest reads passed for all four full references.
- The immutable controller on `192.168.31.31:8200` was recreated from the final Seed digest and reached `healthy`. Existing PostgreSQL, named volumes, administrator-password state and runtime configuration were retained; LAN health and the two public registry prefixes in `app.js?v=8` passed post-recreate checks.

## Diagnostics, Host Maintenance and Verified Recovery (Published)

- On 2026-09-11, TaskHub added authenticated Web diagnostics for local and remote work nodes: Agent events, bounded recent container logs, SSH/Docker operation history, last error, CPU/memory/disk load and active/total slots. The one-click action exports a sanitized ZIP bundle without environment files, credentials, model tokens or business backups.
- Physical hosts now have active, draining, maintenance and disabled states. Non-active or resource-degraded hosts are removed from scheduling; a periodic strict-host-key check reports Docker/network/resource health and blocks a changed fingerprint. Operators can rebuild selected or all nodes on another available host while preserving source volumes for rollback.
- PostgreSQL now stores one fingerprint binding the database to the configuration-encryption master key. Windows and Linux backups include the full database, TaskHub data volume, deployment configuration and rollback images. Restore verifies file hashes, the backup key fingerprint and the fingerprint extracted directly from the PostgreSQL dump before stopping Compose or clearing an allowed TaskHub volume, then verifies the restored database again before starting the controller.
- Feature and release-fix commits are `2e1ece5`, `c81cef7`, `103196a` and image-source commit `4c25aae`. Full Python verification passed with `201 passed, 8 skipped`; real Chrome at 1440, 680 and 390 pixels passed responsive layout, overflow and JavaScript-error checks. PowerShell 5.1 parsing, Bash syntax, a live Node Agent diagnostics container, a full Windows backup and an original-volume restore all passed on `.31`.
- The final `linux/amd64` fixed tags are public and identical across both registries. Seed manifest digest is `sha256:fde9a0efeb615f90278cd41c505828ca86faa76b0c4a0f7d2c6d4a232c519371`; Node manifest digest is `sha256:8d1fd78212c882eb54a8c1bcc57cd1a9aaa9b7a7283cce777ad21a413c80712c`. Anonymous manifest reads passed for all four references. OCI provenance is disabled in release builds for Aliyun ACR compatibility.
- The immutable controller runs the final Seed digest at `192.168.31.31:8200`, is healthy after backup/restore and final recreation, and serves `styles.css?v=27` plus `resource-center.js?v=20`. The validated recovery set is `C:\taskhub-seed\backups\formal-c81cef7`; it contains sensitive material and must remain on controlled encrypted storage. Temporary registry authentication files and the Node smoke container were removed.

## Public Image Download Placement (Rapid Deployment)

- On 2026-09-11, the public-image download disclosure was removed from the login page only. The login card returned to its original 430px desktop width, while authenticated system configuration retains the GHCR/Aliyun ACR Seed and Node references plus copyable pull commands.
- The frontend design document and both version-controlled and installed `taskhub-frontend-design` skills retain this exact placement boundary. Static assets are `styles.css?v=30` and `app.js?v=10`.
- Real Chrome checks passed at 1440, 680 and 390 pixels; `tests/test_task_center.py` passed with 11 tests, all frontend JavaScript syntax checks and `git diff --check` passed. The runtime-only update is deployed to `192.168.31.31:8200`, where health is normal, the login region has no download disclosure and the system-configuration region has exactly one. Rollback files are at `C:\taskhub-seed\backups\system-download-restore-pre-20260911T052003Z`.

## HTTPS, Access Security and RBAC (Release Candidate)

- Username/password login now supports administrator, project owner, developer and read-only auditor roles. API middleware is the authorization source of truth; the frontend also removes unauthorized mutation forms.
- Persistent session activity provides a 30-minute idle timeout, 12-hour absolute timeout and immediate logout revocation. Login failure limiting defaults to five failures in 15 minutes followed by a 15-minute lock. Rotating the persistent signing key revokes all active sessions.
- Authentication outcomes, access denials and authenticated management mutations enter the redacted operational audit without request bodies, cookies, passwords, keys or tokens. CSP, frame denial, MIME-sniff prevention, no-referrer and browser capability restrictions apply to every response.
- Formal `deploy/release` installations default to direct Uvicorn TLS with Secure/SameSite cookies. Initializers create a replaceable self-signed bootstrap certificate; deployment guides require an enterprise/public CA certificate before broader exposure.
- The formal controller no longer mounts `/var/run/docker.sock`. It connects to an internal `ghcr.io/tecnativa/docker-socket-proxy:v0.5.0` that exposes only required Docker API groups and publishes no host port. Existing rapid `.31` Compose remains on the legacy direct-socket layout until an explicit formal-stack migration.
- Candidate static assets are `styles.css?v=31`, `app.js?v=11` and `resource-center.js?v=21`. The platform security disclosure owns user inventory/editing and signing-key rotation; public image downloads remain only in authenticated system configuration.

## Card-Based Resource Configuration (Rapid Preview)

- On 2026-09-11, physical hosts, schedulable Agents, managed node containers and platform status were changed from wide daily-status tables to compact two-column object cards. Card headers carry identity and state, card bodies carry operational facts, and card footers carry object actions.
- Remote-host admission, node creation and managed platform parameters are grouped into business-topic configuration cards. Migration, diagnostics, backup and audit remain secondary disclosures. The development-run production-line input is hidden while the internal `default` compatibility value remains unchanged.
- The runtime-only preview is deployed to `192.168.31.31:8200` with `styles.css?v=32` and `resource-center.js?v=22`. Per operator direction, no additional functional test or visual acceptance was run; only HTTP health and served asset markers were checked.
- Rollback files are at `C:\taskhub-seed\backups\cards-pre-20260911T144147`. The preview modifies files inside the current container and will be lost if that container is recreated before the changes are included in a rebuilt image.
- The development composer follow-up places the requirement textarea on a full-width row and moves status plus the start action into a dedicated footer; the desktop action is right-aligned and the phone action is full-width. It is deployed as `styles.css?v=33`; rollback files are at `C:\taskhub-seed\backups\composer-layout-pre-20260911T144509`.

## System Configuration Disclosure Hierarchy (Rapid Preview)

- On 2026-09-11, the five primary system-configuration disclosures were strengthened as navigation rows with stable order numbers, responsibility text, health-summary pills, bounded expand controls and an accent marker on the open row. Secondary disclosures now render as fully bordered rounded function cards rather than table-like separators.
- Object, configuration, model and role-readiness cards now share a 14px title, 13px fact-value and 12px description/label/metadata hierarchy. The phone layout keeps the primary identity and expand control on the first row and moves long health summaries to a second row.
- The runtime-only preview is deployed to `192.168.31.31:8200` as `styles.css?v=34`. Per operator direction, no additional test or visual acceptance was run during deployment; only the LAN health response, served asset marker and container health were checked.
- Rollback files are at `C:\taskhub-seed\backups\system-config-folds-pre-20260911T150233`. The preview modifies files inside the current container and will be lost if that container is recreated before the changes are included in a rebuilt image.

## System Configuration Detail Alignment (Rapid Preview)

- On 2026-09-11, the diagnostic ZIP download link was normalized to the same 12px compact secondary-action typography as its neighboring diagnostic button. Seed backup/recovery content now follows the 13px body, 12px supporting/monospace hierarchy.
- The Seed external URL and Node Agent callback URL remain side by side through medium-width layouts and stack only below 560px, removing the empty area created by an unnecessarily tall configuration row.
- The runtime-only update is deployed to `192.168.31.31:8200` as `styles.css?v=35`. Per operator direction, no automated or visual acceptance suite was run; LAN health, served asset markers, relevant CSS selectors, container health and `git diff --check` passed.
- Rollback files are at `C:\taskhub-seed\backups\ui-fix-pre-20260911T151209`. The preview modifies files inside the current container and will be lost if that container is recreated before the changes are included in a rebuilt image.

## Card Configuration Formal Image Release

- The card-based system-configuration frontend, hidden production-line field, disclosure hierarchy and v35 typography/alignment fixes were released from source commit `5b4ce12e42ad1e23905270e9ebd9c4cca502eed8`.
- Release verification passed with 206 Python tests and 8 skips. The opt-in real-Chrome system-configuration regression passed at 1440, 680 and 390 pixels, including the diagnostic download typography, backup body typography and responsive address-column assertions.
- The public `0.1.0-alpha` tags were overwritten in both registries with matching `linux/amd64` manifests. Seed digest is `sha256:28ae765419f7ad3671dab3b6963e05f27141db263f7a8e8131080a8800790974`; Node digest is `sha256:53eb01fcfe61148b52360e1158ecc40f5b06ffb9154a71f1c741551549f23133`. Anonymous manifest reads passed for all four references.
- The controller at `192.168.31.31:8200` now runs the released Seed digest and is healthy. The existing administrator-password state, PostgreSQL data and named volumes were retained; authentication remains configured and PostgreSQL contains 11 public application tables.
- The validated pre-release recovery set is `C:\taskhub-seed\backups\formal-pre-5b4ce12`; all recorded SHA-256 checks passed. It contains sensitive configuration and business data and must remain on controlled encrypted storage. Temporary registry authentication files and push tasks were removed after publication.

## Durable Node Delivery and Upgrade Release

- On 2026-09-11, remote node image distribution, durable staged background creation and node image upgrade/automatic rollback were released from source commit `abbd32e5ec28c1f752e3b175ea908eceb497381f`. Seed startup resumes persisted create/upgrade operations, can adopt an already healthy container, and records operation identifiers, attempts, phases, percentages and target images.
- The upgrade path validates the target image platform and digest, preserves the node-specific credential and data volume, commits only after Agent health succeeds, and automatically restores the previous image on failure. The system-configuration UI presents creation and upgrade progress in the established card typography and responsive layout.
- Verification passed with `213 passed, 8 skipped`; the opt-in real-Chrome regression passed at 1440, 680 and 390 pixels with no page overflow. Changed-file Ruff, all frontend JavaScript syntax checks and `git diff --check` passed.
- The fixed public `0.1.0-alpha` tags were overwritten in both registries. Seed digest is `sha256:ba314f197658539af47a959f8a8a95c052c009ed53548b2a4afe29d5120d8955`; Node digest is `sha256:3b37ddf5392d5dea7dbff5d0d5fada87d91c9750685a23fd11157dd7689d5104`. Authenticated manifest reads confirmed matching `linux/amd64` descriptors for all four GHCR/Aliyun ACR references.
- Registry credentials were held only in a temporary Docker CLI configuration on the `.31` build host and were removed after publication. No credential was added to Git, documentation or image layers. The running `.31` controller was not recreated as part of this image-only publication.

## Project-Scoped Preproduction Settings Deployment

- On 2026-09-11, the immutable controller at `192.168.31.31:8200` was recreated from local `taskhub-seed:0.1.0-alpha` image ID `sha256:acdabccce3aeeb6d3bf996cca74dc9e257956abef01b8628bc0ed0afdc4a1bcf`, built from feature commit `601cc11b4fca4ad60168f0dd23fd0287e9305a31`. Registry images were not changed.
- The existing PostgreSQL container and named volumes `taskhub-seed_taskhub-data` and `taskhub-seed_postgres-data` were retained. Both containers are healthy, LAN health returns `{"status":"ok","orchestrator":"langgraph"}`, and the controller serves `styles.css?v=38`, `app.js?v=12` and `resource-center.js?v=25`.
- The complete pre-update recovery set is `C:\taskhub-seed\deploy\release\backups\20260911T134441Z`. The prior Seed image is additionally tagged locally as `taskhub-seed:rollback-20260911T214426`; both recovery sources contain operational or sensitive data and must remain on the controlled host.

## Project Git Repository Management Release

- On 2026-09-11, project-scoped Git repository management was released from image-source commit `864e3cc8e88b8c5f93499d6c58dc8a371aa353cf`. The development-flow project card now persists the remote repository and base ref, rejects URL-embedded credentials, tests connectivity, rolls back a failed remote update and requires a successful Git preflight before starting a delivery run.
- Verification passed with `217 passed, 8 skipped`; the opt-in real-Chrome responsive regression passed. JavaScript syntax, changed-file Ruff and diff checks also passed.
- Complete `linux/amd64` Seed and Node images were rebuilt with OCI provenance disabled. Seed digest is `sha256:e528e106fa8b8be9ba64cdccaa3a63ac0d1f9154fbfc4315beaa20b73bbb58f2`; Node digest is `sha256:42d4d3c6dc39d7803437bf4b6892fd6e8ecd79a81ddbb1c7d0bee665754a1dec`.
- The fixed `0.1.0-alpha` tags were overwritten in both GHCR and Aliyun ACR. Authenticated manifest reads confirmed that each repository exposes the expected digest and that the two registries are byte-identical for each image.
- The controller at `192.168.31.31:8200` now runs the released Seed digest and is healthy. The PostgreSQL container and existing named volumes were retained; the served frontend assets are `styles.css?v=39`, `app.js?v=13` and `resource-center.js?v=25`.
- The latest complete backup is `C:\taskhub-seed\deploy\release\backups\20260911T152347Z`. It contains sensitive configuration and business data and must remain on the controlled host. Temporary build and registry-authentication directories were removed after publication.

## Productized Delivery Phase 1

- On 2026-09-12, Phase 1 added append-only Requirement intake, structured ProductSpec drafts, one consolidated product decision, review/approval governance, immutable approved versions, ChangeRequest-backed revisions and field-level version diffs.
- With `TASKHUB_PRODUCTION_ORCHESTRATION_ENABLED=true`, a run cannot start without an explicitly approved ProductSpec ID and version. The server rebuilds the implementation source from the immutable requirement and supplements, and the LangGraph state preserves the exact binding. The flag remains off by default for legacy compatibility.
- The development-flow ProductSpec card presents raw and structured content, decisions, state, version selection and diffs. Real Chromium layout checks passed at 1440, 680 and 390 pixels without horizontal overflow; targeted API/static tests passed.
- Phase 1 did not produce ProjectContract files or a real task DAG; ProjectContract was delivered in Phase 2.

## Productized Delivery Phase 2

- On 2026-09-12, Phase 2 added DB-backed, versioned ProjectContract records with draft, review, active, superseded and rejected lifecycle states.
- Five official profiles are available: `fullstack-web`, `backend-api`, `frontend-spa`, `python-service` and `worker-service`. New projects select a profile; attached repositories receive an inferred draft for owner review.
- Every contract renders exact `.taskhub/project.yaml`, `.taskhub/architecture.yaml` and `.taskhub/acceptance.yaml` documents. Approved contract data and documents are injected into planner and worker context without TaskHub directly editing a managed project.
- Executable gates cover directory/module boundaries, prohibited dependencies and cycles, complexity, cross-layer data access, interface digest/client consistency, migration ordering/rollback, secrets, binaries/licenses, Docker/Compose health, declared quality commands and required build artifacts. Manual Markdown rules require explicit evidence.
- With productized orchestration enabled, implementation requires both an approved ProductSpec and active ProjectContract and stores their exact identifiers and versions on the run. Contract gates become structured acceptance evidence.
- The workflow UI has a responsive ProjectContract card for template, lifecycle, facts and gate results. Continue strictly from Phase 3 (DAG and persistent scheduling). Phase 2 was source-only: push `.3 Git` and GitHub, with no deployment, Docker build or registry upload.

## Productized Delivery Phase 3

- On 2026-09-12, Phase 3 added versioned, DB-backed ExecutionPlan, ProductionTask, ExecutionBatch, TaskAttempt and DAG execution snapshots bound to exact ProductSpec and ProjectContract versions.
- Planner validation rejects cycles, unknown dependencies, implementation tasks without executable acceptance, invalid data flow, unsafe parallel path overlap, contract path violations, unverifiable oversized work and unavailable capabilities without an installation strategy.
- The persistent scheduler computes Ready state from dependencies, frozen contracts, governance state, healthy capabilities and locks; creates dynamic batches; enforces global/project budgets fairly; delegates node slots, priority, task stickiness and failover to NodeScheduler; and persists assignment and waiting reasons.
- Each attempt gets an idempotency key and isolated Git worktree at the exact integration base. Parallel commits are integrated by the controller. Restart recovery reuses persisted successful results without running the command again and creates a new attempt after execution failure.
- The development flow adds a read-only text/card execution-plan view with counts, batches, task dependencies, node assignments, locks and waiting reasons. It deliberately does not introduce a graph canvas before Phase 3A.
- Verification: full Python suite `244 passed, 10 skipped`; real Google Chrome passed at 1440, 680 and 390 pixels with no page-level horizontal overflow; Ruff, JavaScript syntax, architecture line limits and diff checks passed. Static assets are `styles.css?v=42` and `app.js?v=16`.
- Continue strictly from Phase 3A. This phase is source-only: push `.3 Git` and GitHub; do not deploy, build Docker or publish registry images.

## Productized Delivery Phase 3A

- On 2026-09-12, Phase 3A established independent `taskhub-web` with React, TypeScript, Vite, React Flow and TanStack Query. FastAPI serves its production build at `/canvas/`; the legacy three-page frontend remains intact and links to the canvas from Development Workflow.
- ProductionTopology has independent memory/PostgreSQL persistence, versioned draft/validating/invalid/active/superseded states, saved layout and viewport, typed edges, content-digest concurrency checks and one active version per project.
- Server validation covers endpoint/type integrity, duplicate IDs/bindings, directed cycles, unique controller path, execution/test paths, resource health, role/capability compatibility and same-role fallback. Activation supersedes the previous version; new topology versions do not migrate running work.
- Active topology execution and verification targets restrict NodeScheduler eligibility. Without an active topology, legacy scheduling remains unchanged. The canvas overlays the newest run, DAG task state and node health/slot occupancy, and can submit a requirement into the established productization flow.
- Desktop operations include drag/connect, selection, pan/zoom, minimap, auto-layout, undo/redo and context menus. Toolbar, Shift+F10 and list editing provide equivalent non-pointer paths; the list owns inventory-backed resource binding and resource-pool membership on narrow screens.
- Verification: full Python suite `249 passed, 13 skipped`; TypeScript/Vite production build passed; real Google Chrome passed canvas operations and page-overflow checks at 1440, 680 and 390 pixels. Changed-file Ruff, legacy JavaScript syntax, architecture line limits and diff checks passed.
- Phase 3A is source-only: push `.3 Git` and GitHub. Do not deploy, build Docker or publish registry images. Continue strictly from Phase 4.

## Productized Delivery Phase 4

- On 2026-09-12, Phase 4 added DB-backed ChangeRequest lifecycle records and explainable impact analysis from explicit tasks, changed paths, unfinished work and dependency descendants.
- Applying an approved request creates ExecutionPlan version N+1. Impacted tasks receive replacement IDs and new attempts; completed unaffected tasks retain their validated result and evidence through explicit reuse links. Historical plans, tasks and attempts are never overwritten.
- Verification tasks in the affected subgraph form the integration regression scope. Scheduler batch IDs include the plan version, so restart and revised execution cannot collide with earlier batches.
- Automatic revisions respect each project's maximum revision count and escalate to a proposed request for human approval once exhausted. Manual requests support propose, approve, apply and reject through CSRF- and permission-protected APIs.
- Development Workflow now contains a compact ChangeRequest configuration/presentation card. Its impact, reuse and regression facts come from the server, with responsive coverage at 1440, 680 and 390 pixels.
- Verification: full Python suite `254 passed, 16 skipped`; TypeScript/Vite production build, changed-file Ruff, JavaScript syntax, architecture limits, diff checks and real Chrome at 1440, 680 and 390 pixels passed.
- Phase 4 is source-only: push `.3 Git` and GitHub. Do not deploy, build Docker or publish registry images. Continue strictly from Phase 5.

## Productized Delivery Phase 5

- On 2026-09-12, Phase 5 added trusted, versioned CapabilityPack inventory for frontend style, components, layout and brand, plus the broader architecture/testing/security/delivery type contract.
- Built-in packs provide three compatible frontend combinations. Admin imports remain drafts until explicitly trusted; traversal, credentials, missing licensing, undeclared executable permissions and oversized manifests are rejected. Packs are data and declared validators, not an arbitrary controller-side execution path.
- Each frontend ProductSpec version requires an exact CapabilityPackLock and compiled ProjectDesignContract. The execution plan and every generated task retain the exact lock/design versions; worker context receives tokens, component/layout/brand, responsive/accessibility rules, viewports and required validation evidence.
- A revised ProductSpec requires a new lock even when package versions are unchanged. Design-pack changes create a draft and migration tasks; stale drafts, disabled packs and newly incompatible combinations cannot activate, and old lock/design versions remain historical.
- Development Workflow owns recommendation, preview, lock and migration cards. Platform Settings owns global inventory/import/trust/availability. Static assets are `styles.css?v=45`, `app.js?v=18` and `capability-center.js?v=1`.
- Verification: full Python suite `263 passed, 19 skipped`; TypeScript/Vite production build, changed-file Ruff, JavaScript syntax, architecture limits, diff checks and real Chrome at 1440, 680 and 390 pixels passed.
- Phase 5 is source-only: push `.3 Git` and GitHub. Do not deploy, build Docker or publish registry images. Continue strictly from Phase 6.

## Seed, Host Pool and Node Provisioning Realignment

- On 2026-09-20, system configuration was realigned around the operator journey “confirm Seed → establish host pool → deploy TaskHub node”. The primary disclosures are Seed Status, Host Pool and TaskHub Nodes; Model Services and Advanced Settings remain available with secondary visual emphasis.
- Available admitted remote hosts are preferred when opening the node-creation target selector. Seed-local placement remains supported but is explicitly labeled for single-host or test use.
- Online release initialization pulls only the Seed control-plane stack. The Node image is kept as a fully qualified registry reference and pulled on demand by the target host during node creation. Offline bundles still include and validate the Node image for disconnected SSH distribution.
- Capability inventory content uses the same 16px desktop and compact mobile inset as neighboring management-card grids, preventing its cards and import form from visually drifting toward the sidebar.
- Static assets are `styles.css?v=48`, `app.js?v=20` and `resource-center.js?v=26`.
- At the operator's request, the four static frontend files were copied into the running immutable controller at `192.168.31.31:8200` on 2026-09-20. The controller remained healthy and the LAN endpoint serves the v48/v20/v26 markers. This is a runtime-only update and will be lost when the container is recreated unless a new Seed image is built.
- Rollback files are at `C:\taskhub-seed\backups\system-realignment-pre-20260919T175915Z`; PostgreSQL, administrator credentials, volumes, nodes and runtime configuration were not changed.

## Single-Seed Product Scope

- On 2026-09-20, the product scope changed to a single Seed: TaskHub nodes run only in the Seed-connected Docker Engine.
- System configuration removes the physical-host pool, SSH admission, target-host selection, cross-host rebuild/migration and remote-node upgrade workflows. Existing persisted multi-host records remain untouched for compatibility and audit.
- First-run readiness now has six steps and no longer requires a physical host. Online initialization preloads the Node image alongside Seed, PostgreSQL and the Docker proxy.
- Static asset markers are `styles.css?v=49`, `app.js?v=21`, `resource-center.js?v=27` and `onboarding.js?v=3`.
- The runtime files and release initializers were copied to `192.168.31.31:8200`; the controller was restarted healthy and serves the new markers. Rollback files are in `C:\taskhub-seed\backups\single-seed-pre-20260920`. This remains a runtime container patch until a replacement Seed image is built.
- Capability inventory card facts and summary now share a 15px internal inset; the stylesheet marker is `styles.css?v=50`.
- Codex device authorization now uses the saved model-card proxy before the global OpenAI proxy, terminates after 20 seconds without a code, and reports network/proxy and ChatGPT device-code permission guidance; browser polling errors are surfaced instead of leaving the card on “正在请求设备验证码”. The resource script marker is `resource-center.js?v=28`.
- Account authentication now presents only the URL and device code actually emitted by `codex login --device-auth`. The backend reads arbitrary stdout chunks rather than newline-delimited output, so a CLI prompt without a trailing newline is parsed immediately. The resource script marker is `resource-center.js?v=30`.
- Codex CLI `0.153.4` emits device codes with unequal segment lengths (observed 4+5). Parsing strips ANSI codes and only accepts a value after the “one-time code” prompt or on a standalone line, preventing `COMMAND-LINE` from being mistaken for a code. The saved model-card proxy was verified against both the OpenAI API and device-code endpoint.
- Starting account authorization now supersedes any active session for the same model, terminates its Codex CLI process and issues a fresh code. The UI identifies the Seed controller/model target and ignores polling results from superseded sessions. The resource script marker is `resource-center.js?v=31`.
- The device-code row now ends with an accessible copy button and in-place success/failure feedback, including a fallback for browsers without the Clipboard API. Static markers are `styles.css?v=52` and `resource-center.js?v=32`.
- Multi-card model saving now explains the complete five-role routing invariant, validates missing/conflicting primary and backup assignments before submission, and renders structured API validation errors as readable text. Static markers are `styles.css?v=53`, `app.js?v=22` and `resource-center.js?v=33`.
- Project onboarding now requires an explicit Git remote URL and Seed-local managed checkout path for both repository creation and repository attachment. Discovery only prefills editable values; the backend enforces the configured authority and managed-root boundaries before creating or cloning. The application script marker is `app.js?v=23`.
- Every saved model card can now test its own credentials and retrieve a normalized model catalog. API cards use `GET /models`; ChatGPT account cards use `codex debug models` in their isolated account home. The card renders the catalog as a selector that fills its model field. Static markers are `styles.css?v=54` and `resource-center.js?v=34`.
- API-key model cards now encrypt and persist their per-card secrets without re-reading them from the legacy secret map; testing an unsaved card returns a controlled message instead of a server error. The shared request helper also converts non-JSON server failures into an HTTP status message. The application script marker is `app.js?v=24`.
- The model-catalog and MiniMax save fixes were runtime-deployed to `192.168.31.31:8200` on 2026-09-20. The controller restarted healthy and serves `styles.css?v=54`, `app.js?v=24` and `resource-center.js?v=34`; rollback files are at `C:\taskhub-seed\backups\model-catalog-minimax-pre-20260920`. Verification: `283 passed, 19 skipped`, changed-file Ruff/JavaScript syntax/architecture/diff checks passed. This direct container patch remains ephemeral until a replacement Seed image is built.
- Unsaved API model cards can test the current address, proxy and transient API Key before a model is selected. The returned catalog fills the model field, breaking the previous save/test dependency cycle without persisting the test key. The resource script marker is `resource-center.js?v=35`.
- Unsaved ChatGPT account cards can likewise start device authorization with their current model ID and proxy, retain the draft card after authorization, and then read their isolated Codex model catalog before the card is saved. The resource script marker is `resource-center.js?v=36`.
- The draft-card test flow was runtime-deployed to `192.168.31.31:8200`; the controller restarted healthy, serves `resource-center.js?v=35`, and its deployed backend/static hashes match source. Rollback files are at `C:\taskhub-seed\backups\draft-model-test-pre-20260920`. Verification: real Chrome passed the unsaved MiniMax catalog flow and the full suite reported `284 passed, 19 skipped`.
- The unsaved ChatGPT-account authorization extension was then runtime-deployed to the same Seed. It serves `resource-center.js?v=36`, remains healthy, and has rollback files at `C:\taskhub-seed\backups\unsaved-account-model-pre-20260920`. The final full suite reported `285 passed, 19 skipped`.
- Provider tests now dispatch by service adapter. MiniMax no longer assumes an OpenAI `/models` endpoint: it validates against the official regional Token Plan endpoint and returns the maintained official text-model catalog. API keys containing whitespace are rejected with a specific format message. Model-card lower content is compacted into left-side role routing and right-side actions/status on wide screens. Static markers are `styles.css?v=55` and `resource-center.js?v=37`.
- The provider-adapter and compact model-card layout were runtime-deployed to `192.168.31.31:8200`; the controller restarted healthy and serves the v55/v37 markers. Rollback files are at `C:\taskhub-seed\backups\provider-adapter-layout-pre-20260920`. Real Chrome passed at 1440, 768, 680 and 390 pixels, and the full suite reported `287 passed, 19 skipped`.

## Automatic Delivery Progression

- On 2026-09-20, routine plan approval and publication approval were removed from the active workflow. New runs proceed from planning directly to implementation and from successful supervision directly to publication.
- The task-center projection and browser flow contain nine visible stages. Human actions remain only for genuine recovery, missing evidence, revision-limit, cancellation and project-rebinding decisions.
- Legacy `plan_approval` and `merge_approval` graph nodes and enums remain compiled for checkpoint compatibility. Startup recovery automatically approves those retired waits so pre-upgrade tasks can continue.
- The application script marker is `app.js?v=25`.
- The workflow/service/static patch was copied into the running controller at `192.168.31.31:8200`; the container restarted healthy, live hashes match source and the endpoint serves `app.js?v=25`. Rollback files are at `C:\taskhub-seed\backups\automatic-flow-pre-20260920`. This direct container patch remains ephemeral until a replacement Seed image is built.
- Verification: full Python suite `283 passed, 19 skipped`; changed-file Ruff and JavaScript syntax checks passed.

## Configurable Git Repository Service

- On 2026-09-20, global Git authority settings were added under Advanced Settings → Manageable Platform Parameters. Administrators can configure and test the SSH user/host, port, authoritative bare-repository root, Seed checkout root and an optional encrypted private key without first saving the draft.
- Project create and attach surfaces show the active Git service and link directly to its configuration. Repository discovery failures no longer prevent manual entry. Created and cloned repositories retain a stable SSH command using the managed key and known-hosts files, so later fetch and publication use the same identity.
- Static asset markers are `styles.css?v=56`, `app.js?v=26` and `resource-center.js?v=38`. Verification passed with real Google Chrome, release-delivery tests, Ruff, JavaScript syntax, diff checks and the full suite: `284 passed, 19 skipped`.
- The backend/static patch was runtime-deployed to `192.168.31.31:8200` through the Windows SSH account `g`. The controller restarted healthy, all 12 deployed file hashes match source, and the live page serves v56/v26/v38. Rollback files are at `C:\taskhub-seed\backups\git-service-config-pre-20260920`. Existing PostgreSQL data, volumes, administrator state and project data were not changed. This direct container patch remains ephemeral until a replacement Seed image is built.
- Passwordless access from `.31` to the Git authority `.3` is now configured with separate Ed25519 identities for the Windows maintenance account and the Seed service. Only their public keys were added to `gryps@192.168.31.3`; the Seed private key is stored at `/var/lib/taskhub/config/ssh/authority_key` with mode `0600` and is also encrypted in managed platform configuration. Effective Git settings are `gryps@192.168.31.3:22`, authority root `/home/gryps/git`, and managed checkout root `/var/lib/taskhub/repositories`. Host SSH, container SSH and the TaskHub Git connection test all passed after a healthy controller restart; temporary private-key copies were removed.

## Execution Node Coding Runtime Repair

- On 2026-09-21, local-Docker execution nodes were repaired to enable `TASKHUB_NODE_CODING_ENABLED`, use `seccomp=unconfined` only for the execution role, and mount an isolated `config/model-accounts/node-runtime` subdirectory. The generated runtime contains only enabled OpenAI coder cards and their required account/API credentials; Seed database, administrator, Git and session secrets are excluded.
- Production Compose now defaults to `TASKHUB_WORKER_MODE=git`; legacy deployments without TLS variables retain HTTP instead of receiving unreadable default certificate paths. Docker image builds accept an optional `NPM_REGISTRY` alongside the existing Debian mirror arguments.
- The coding router now uses each model card's proxy before the global OpenAI proxy. This fixed the gap where device login and connection tests succeeded but actual Codex coding failed with `ProxyRequiredError`.
- A temporary overlay release was deployed to `.31` for acceptance. Both execution nodes reported coding, workspace-write sandbox, Git and Python capabilities. A real Codex task changed an isolated `value.txt` on `work-01`, then `work-02` executed a Python assertion against the transferred workspace; the result was `DEVELOPMENT_SMOKE_OK`.
- Final source verification before Git publication: `288 passed, 19 skipped`; real Chrome system-configuration acceptance, focused Ruff, JavaScript syntax, diff checks and the React/Vite production build passed.

## Phase 7 Quality Baseline and Project Preflight

- On 2026-09-21, the active backend converged to the single-Seed product boundary: physical-host and remote-node routers, background monitoring and reconciliation are no longer mounted. Historical modules and diagnostic archive members remain for rollback compatibility, but return no active legacy inventory.
- Repository-wide Ruff is clean. `make check` selects the project virtual environment and runs Python tests, React unit tests and the Vite production build. GitHub Actions adds Python 3.12/3.13/3.14, PostgreSQL, frontend and real-browser jobs.
- A release acceptance test now uses a real bare Git authority, real worktree, local quality command, Git publication and PostgreSQL checkpoint. It passed in an isolated container and verified that a fresh controller restores the completed run without creating a duplicate commit.
- `ProjectPreflightService` is the single authority for project startup readiness. The report covers repository reachability, effective model configuration, online Seed-local execution, active-contract requirements, quality commands and declared acceptance capabilities. The Development Workflow renders six actionable checks and disables “开始流程” until blocking checks pass.
- Static asset markers are `styles.css?v=57` and `app.js?v=27`. The next Phase 7 work item is the unified exception and recovery-action center; no live deployment or image publication has been performed for this source batch.
- The unified exception center projects waiting, blocked and failed entries directly from workflow checkpoints and the task index. It groups project, implementation, acceptance, evidence, publication and workflow failures by severity and opens the checkpoint-owned recovery action in task detail; it does not introduce a second mutable lifecycle.
- Canonical task-stage filters now include their blocked sub-states. Memory and PostgreSQL indexes expose the same attention query, including restart recovery. Static markers are `styles.css?v=58`, `app.js?v=28` and `task-center.js?v=14`; real Chrome passed at 1440/390px and PostgreSQL index/restart/browser tests passed.
- The next Phase 7 work item is the unified evidence center, followed by model operations and backup/recovery. No deployment, image build or image publication has been performed for this source batch.
- `EvidenceCenterService` now derives four-gate completeness, test/evidence pass counts, SHA-256 artifact integrity, missing evidence and source/commit lineage from authoritative run checkpoints. The Task Center presents the read-only projection and opens the existing task evidence detail; real Chrome passed at desktop and 390px widths.
- `ModelOperationsService` combines provider billing/runtime circuit state with the latest checkpoint-owned model traces, reporting calls, role distribution, latency and fallbacks without exposing credentials. The system-configuration panel degrades independently from model-card editing; its real Chrome regression covers cooldown and quota states.
- Release kits now include `verify-backup.sh` and `verify-backup.ps1`. Backup creation invokes the verifier before success; operators can rerun it without restoring. It verifies checksums, the configuration-key fingerprint, the PostgreSQL backup identity and the TaskHub data archive on Ubuntu or Windows.
- Static markers are `styles.css?v=60`, `app.js?v=28`, `task-center.js?v=15` and `resource-center.js?v=39`. The next Phase 7 item is full regression and release/security documentation closure. No deployment, image build or image publication has been performed for this source batch.
- Phase 7 source closure passed repository-wide Ruff; `302 passed, 23 skipped` Python tests; four React tests; the Vite production build; all 15 opt-in real-Chrome browser cases; and six focused PostgreSQL/restart/release cases. GitHub Actions YAML, shell syntax, PowerShell BOM requirements, diff whitespace and a redacted credential-pattern scan passed. The only initial browser mismatch was an obsolete assertion expecting an active plan after automatic completion; it now correctly expects the completed state.
- Temporary PostgreSQL used on `.31` ran with `--rm`; its SSH tunnel was stopped and the container stop returned its name with no retained listing. The source is ready for Git publication. Building/publishing replacement Seed and node images, deploying them, and running a destructive target restore drill remain explicit release actions.

## Project Preflight Remediation

- On 2026-09-22, attached projects gained an editable “质量与验收” section under the current-project repository card. Project owners can update quality commands, optional acceptance commands and the isolated PostgreSQL requirement after attachment; saving refreshes the authoritative project preflight immediately. Static markers are `styles.css?v=61` and `app.js?v=29`.
- The formal Seed Compose now injects its PostgreSQL administrator DSN only into test and preproduction node containers. Execution nodes do not receive it. The three live acceptance-capable nodes reported PostgreSQL 16.15 connectivity and create-database permission after credential rotation/recreation.
- Existing live projects were configured without modifying their managed source repositories: `douyin-market-automation` uses `python3 -m pytest -q`, and `ecommerce-operations-platform` uses its repository-owned `npm test` script. Both projects' `quality_commands` and `acceptance` preflight checks returned `passed`.
- Verification passed with repository-wide Ruff, `306 passed, 23 skipped`, four frontend tests, the Vite production build, JavaScript syntax/diff checks, and the new quality-remediation browser flow at 1440, 768 and 390 pixels.
- Source commit `55e5dea784f2bcbbab597491e22b26b1dfc9daeb` was pushed to both `.3 Git` and GitHub. The immutable controller at `192.168.31.31:8200` runs healthy with zero restarts on Seed digest `sha256:b66f57c32620bedf868ace5b361befc356982a1669659d1391dc98817cda2cdf`. Aliyun ACR exposes that manifest directly; the GHCR index contains the same `linux/amd64` manifest. The verified pre-update recovery set is `C:\taskhub-seed\deploy\release\backups\20260921T212313Z`.

## Project Preflight and Upgrade Volume Corrections

- Project preflight now reads the active `.taskhub/acceptance.yaml` contract and evaluates its
  declared command, browser, profile and authentication capabilities against the appropriate
  execution or acceptance workloads. A run can no longer pass the six-item preflight and then be
  rejected immediately for a capability the preflight omitted.
- Windows and Ubuntu release initializers prefer an existing `taskhub-seed_*` data or PostgreSQL
  volume when an unused standard-named volume also exists. This preserves the active model-account
  and repository data used by recreated coding nodes after upgrades.
- System configuration no longer repeats the Seed and node 01/02 sequence in separate navigation
  cards. The numbered interactive disclosures remain the single source of navigation, while the
  Seed card's readiness banner carries incomplete onboarding progress. The stylesheet marker is
  `styles.css?v=62`; real Chrome passed at 1440, 768, 680 and 390 pixels without page overflow.
- The first managed ecommerce run exposed that the unified Node Dockerfile was installing Debian's
  Node.js 18 packages even though the documented execution baseline is Node.js 22. The image now
  copies the official Node 22 runtime into the Python 3.12 base and verifies `node` and `npm` during
  the build, preventing project compatibility shims from changing quality-gate behavior.
- Source commits `f459604dc2cef2d673ee331241ed127c705ade56` and
  `25e123e1546615f156622edd20a994edf23b7315` were pushed to both `.3 Git` and GitHub. Verification
  passed with `309 passed, 26 skipped`, four React tests, the Vite production build and real Chrome
  at 1440, 768, 680 and 390 pixels.
- The controller at `192.168.31.31:8200` runs the healthy Seed built from `f459604`, serves
  `styles.css?v=62`, and no longer emits the duplicate setup-flow markup. The pre-release backup is
  `C:\taskhub-seed\deploy\release\backups\20260922T120804Z`; the prior Seed is retained as
  `taskhub-seed:rollback-pre-f459604`.
- GHCR and Aliyun ACR both expose the rebuilt `0.1.0-alpha` Seed manifest
  `sha256:5ad37d2c6adc2bbe6af2c6c798224d33b239675253f2051a1283a29723b61889` and Node manifest
  `sha256:fd9be1edf02b4634a46bbd079830b87d4bbf2954a610ffc17c2eee6d1297f506`.
  Anonymous manifest reads returned the matching Seed config digest
  `sha256:3ff3d46d85d73276b631ceaa114355a5e26811ac52fe3f76c5f04f90e87089ba` and Node config digest
  `sha256:9256365ae9636d8403bdf6bdf79237bd0a374a5d32442727c5bd7f79612da5b7`. The recreated coding nodes are healthy, report Node.js
  `v22.23.2`, and resumed the blocked BE-012 pilot through TaskHub.
- A quality-command failure leaves the coder's edits uncommitted in its isolated run workspace.
  Revision recovery now retests those edits before invoking the coding model again; a successful
  retest commits the existing implementation with explicit `TaskHub-Recovery: quality-retest`
  provenance. This prevents transient package-registry failures from consuming another model call
  or perturbing otherwise valid work. Verification passed with `310 passed, 26 skipped`, Ruff and
  four frontend tests.
- Seed and Node image builds retry all transient Codex installer download errors, including TLS
  handshake failures, with an explicit connection timeout. This closes a release failure observed
  while publishing the quality-retest fix from the Windows Docker Desktop release host.
- Cross-platform release image builders now forward optional `TASKHUB_BUILD_PROXY` as BuildKit
  `HTTP_PROXY` and `HTTPS_PROXY` arguments. This lets package and Codex CLI downloads use the
  operator-configured egress route without baking the proxy into the resulting image.
- The BE-012 pilot exposed that a timed-out quality command killed only its immediate process and
  left npm/pytest descendants consuming a node. Node and local execution now start each command in
  an isolated process group and terminate the whole process tree on timeout. Project quality
  settings also expose the persisted per-command timeout (1–3600 seconds), so large suites can use
  an intentional limit instead of the 600-second default. The static marker is `app.js?v=30`;
  verification passed with `312 passed, 26 skipped`, focused Ruff, and the descendant-timeout
  regression.
- Model operations now derives provider identity, authentication state, per-card proxy, encrypted
  API credential presence, role assignment and billing probes from the effective dynamic model
  cards. It no longer reports the obsolete fixed Plus/Pro/API settings when cards are active.
  Account quota probes use each card's proxy and API balance probes use each card's credentials;
  the operations cards show the configured display names. The resource script marker is
  `resource-center.js?v=40`; verification passed with `313 passed, 26 skipped`, repository-wide
  Ruff, JavaScript syntax and diff checks.
- The BE-012 pilot also exposed overlapping automatic recovery and manual retry of the same run.
  Run actions are now serialized per run before their state is revalidated, preventing duplicate
  coding or quality jobs in a single Seed process. When an uncommitted recovery retest fails, its
  bounded diagnostics are included in the next coding request so the model can repair the actual
  project failure instead of receiving only the earlier generic feedback.
