# Windows browser acceptance node

Build the candidate package, copy it and `deploy/windows/install-node-agent.ps1` to
`192.168.31.34`, then run the installer in the desktop account that will operate the
browser. The installer creates an isolated environment under `C:\TaskHub`, encrypts
the existing `TASKHUB_NODE_TOKEN` with that account's DPAPI key, and registers an
interactive at-logon scheduled task. Never put the token in the repository or an
installer argument.

Set `BrowserProfileDir` and `BrowserAuthTarget` during installation, then run
`deploy/windows/authorize-browser-profile.ps1` in the same interactive account. Complete the
external login once and close the browser before creating the readiness marker. Node health
reports `browser_profile` and `browser_authenticated`; browser acceptance cannot be scheduled
until both are true.

For a newly registered node, **系统配置 / 验收前置配置** displays the exact command for
its current state with a copy button. Operators do not need to locate this document before
admission can be completed.

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

## Supervisor follow-up in this worktree

Base HEAD: `9243acaf908d76bed4583ab63e0c85b93153921c`. This revision is an
uncommitted worktree patch, as requested; HEAD identifies the base and does **not**
identify the modified candidate. Use `git diff --name-only` and `git diff` to
review the delivered changes. No browser result for the base can validate this
patch. The clean-commit preview guard intentionally remains enabled.

The follow-up changes contract validation, JUnit scenario coverage, scheduler
preflight, the project acceptance gateway, and the project E2E report, with
regression tests in `tests/test_browser_acceptance.py` and `tests/test_scheduler.py`.
JUnit now requires each manifest scenario on each declared browser. Project tests
record completion only after their assertions pass, including a changing retry
countdown and rendered recommended action. JSON and HTML reports list completed
scenarios. The controller checks node eligibility before starting the preview,
and execution repeats the health check before upload.

Validation performed here: 21 browser-contract/report/preview and scheduler tests
passed (the subprocess identity test was deselected). Compilation and
`git diff --check` passed. Workflow test runs stalled in asynchronous waiting and
were interrupted; the isolated structured-browser workflow run timed out.
Explicit E2E execution failed on missing Playwright, with no skips. TCP connection
to `192.168.31.34:8301` failed. Consequently this is not evidence of a deployed
Windows node or a completed browser acceptance cycle.

Outstanding supervisor requirements remain: actual Windows startup/listener/auth
and resource-center evidence; full node/model-switch action coverage; real
artifact upload through re-supervision to publication approval; PostgreSQL
cross-process allocation and restart cleanup; and real Chromium/Edge execution
against the eventual committed candidate. Existing at-logon deployment is not a
Windows startup service. These must not be marked accepted using local unit
results or a two-test pass count.
