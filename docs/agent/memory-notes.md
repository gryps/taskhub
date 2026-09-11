# TaskHub V2 Memory Notes

Updated: 2026-09-11

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
