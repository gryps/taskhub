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
