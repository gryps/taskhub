# Windows browser acceptance node

Native Windows nodes use the authenticated Node Agent contract but run in the interactive desktop
account required by headed Chrome or Edge. They are external test resources, not members of a
physical-host pool.

## Product onboarding

Use **System Configuration → TaskHub Nodes → Attach external Windows test machine**. The wizard:

1. accepts a stable node ID, Windows address, SSH user/port and Agent port;
2. reads and displays the SSH host fingerprint for out-of-band confirmation;
3. verifies Windows and Python over SSH;
4. issues a distinct node credential, transfers the current package and installer, and registers an
   interactive-at-logon scheduled task;
5. admits the node to scheduling only after its authenticated health response returns the same node
   ID.

The submitted SSH private key is not retained. A later repair requires submitting it again. The
virtual environment and pip cache live under `C:\TaskHub`, so reinstalling the same package does not
redownload unchanged dependencies. Reinstallation stops the previous scheduled-task instance before
registering its replacement and returns success only after the authenticated Agent health endpoint
reports the expected node ID; it never leaves a fallback process attached to the SSH session.

## Browser mode

Browser mode additionally requires a browser login target. Install system Chrome and Microsoft Edge,
then authorize the persistent profile in the same Windows desktop account. Health must report
`windows_gui`, `playwright`, `chromium`, `edge`, `screenshot`, `video`, `trace`, `browser_profile`
and `browser_authenticated` before browser acceptance is schedulable.

Windows services run in session 0 and cannot operate an interactive browser. The installer therefore
uses an interactive-at-logon scheduled task. After reboot, sign in to that account and verify Agent
health and the browser profile again.

Projects own browser scenarios in `tests/e2e/` and declare their command, preview lifecycle, browser
matrix, timeout and evidence in `.taskhub/acceptance.yaml`. They must also bind the admitted node ID
in project quality configuration. TaskHub never falls back to an arbitrary Windows node.

## Security and recovery

- Never disable SSH host-key checking.
- Never place the SSH private key or node credential in a repository, installer argument or log.
- Removing the node from TaskHub revokes its credential and stops scheduling but leaves remote files
  for diagnosis. Remote deletion is a separate explicit operation.
- If installation fails, correct the reported Python, package, firewall, browser or reachability
  problem and repeat admission; the failed credential is already revoked.
