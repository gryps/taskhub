# Windows browser acceptance node

Run `deploy/windows/install-node-agent.ps1` as Administrator on `192.168.31.34`.
The installer creates an isolated virtual environment and chooses `D:\TaskHub\jobs`,
falling back to `C:\TaskHub\jobs`. Configure the existing `TASKHUB_NODE_TOKEN` in
the protected Windows service environment before starting the service; never put it
in the repository or installer arguments.

Install Playwright and both browsers in that environment, then verify
`http://192.168.31.34:8301/api/health` with bearer authentication. The registry entry
must retain only the `browser_acceptance` workload. Health must report
`windows_gui`, `playwright`, `chromium`, `edge`, `screenshot`, `video`, and `trace`.

Projects own their scenarios in `tests/e2e/` and declare the portable command,
preview lifecycle, browser matrix, timeout, and required evidence in
`.taskhub/acceptance.yaml`. The agent contains no project-specific scripts.

## Revision verification status

The current workspace cannot connect to `192.168.31.34:8301` and has no
Playwright installation. No Windows deployment, authenticated online status,
autostart, Edge/Chromium pass, or production task evidence is claimed.
The dedicated E2E command fails on missing dependencies; it does not skip.
The project suite manifest covers login and pending approval, structured blocker
metadata, refresh and two independent contexts, automatic retry recovery, and
state-dependent actions. These checks still require a real Windows run; the
deterministic preview adapters do not prove a real model supervision cycle.

Preview startup requires a clean committed worktree. The controller supplies
`TASKHUB_PREVIEW_DSN` (with a schema-only search path) and
`TASKHUB_PREVIEW_STATE_DIR`; the Agent supplies `TASKHUB_TARGET_URL` and
`TASKHUB_GIT_COMMIT` to the test process. JUnit must repeat both values on each
case, identify both browsers, and contain no skipped, failed or empty suites.

Windows services run in session 0. The existing NSSM installer alone does not
establish an interactive desktop: its GUI environment flag is not evidence.
Run `python -m taskhub_v2.node_agent.browser_probe` under the actual service
identity/session. An unavailable headed desktop must leave capabilities false;
provision an interactive runner before accepting this deployment. Restart the
machine and verify the authenticated health endpoint and browser probe again.
Do not replace or regenerate the existing node token.

Remaining preview operational gaps: port ownership is currently local to one
manager process, and abrupt controller termination requires operator cleanup.
Cross-process port leases and restart reconciliation must be completed before
claiming concurrent/restart cleanup acceptance. The unit cancellation test mocks
schema operations; a real PostgreSQL isolation and cleanup run remains required.
