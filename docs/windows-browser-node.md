# Windows browser acceptance node

Build the candidate package, copy it and `deploy/windows/install-node-agent.ps1` to
`192.168.31.34`, then run the installer in the desktop account that will operate the
browser. The installer creates an isolated environment under `C:\TaskHub`, encrypts
the existing `TASKHUB_NODE_TOKEN` with that account's DPAPI key, and registers an
interactive at-logon scheduled task. Never put the token in the repository or an
installer argument.

Install Playwright in that environment and provide system Google Chrome and Microsoft
Edge. The installer does not download a second bundled browser; both system browsers
must pass the interactive screenshot, video, and trace probe. First verify the isolated candidate endpoint on port
`8391` with bearer authentication. The production endpoint remains port `8301` and
must only be promoted after candidate acceptance. The registry entry
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

Windows services run in session 0 and cannot establish the required interactive
desktop. The installer therefore uses an interactive at-logon scheduled task.
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
