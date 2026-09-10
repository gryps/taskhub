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
