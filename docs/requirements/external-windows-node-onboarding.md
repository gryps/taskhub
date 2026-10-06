# External Windows Node Onboarding

## Goal and boundary

TaskHub lets an administrator attach a native Windows test machine without restoring the retired
physical-host pool or distributed-container migration model. SSH is a bounded bootstrap and repair
channel. After installation, Seed schedules work through the authenticated Node Agent HTTP contract.

The product does not discover arbitrary LAN machines, reuse a node across projects without explicit
authorization, manage unrelated Windows software, or treat an SSH listener as proof that an Agent is
healthy.

## Workflow

1. Under **System Configuration → TaskHub Nodes → Attach external Windows test machine**, enter a
   stable node ID, display name, address, SSH user/port, Agent port and one-use private key.
2. Seed reads the SSH host key and requires explicit fingerprint confirmation before authentication.
3. Seed verifies Windows and Python, issues a distinct encrypted node credential, transfers the
   current TaskHub wheel and installer, and starts an interactive-at-logon scheduled task.
4. Seed calls the Agent health endpoint with that node's credential and verifies the returned node ID
   before atomically adding it to the scheduler registry.
5. Projects select the registered node ID in their own quality and acceptance configuration.

The SSH private key is request-scoped: it is not written to inventory, operation logs, API responses
or Git. Removing a node revokes its TaskHub credential and removes it from the scheduler; remote files
remain for diagnosis or a later explicit cleanup action.

## Failure and recovery

- A missing or changed fingerprint blocks installation.
- A failed install revokes the issued credential and removes any partial scheduler record.
- An installed but unreachable Agent remains visibly unavailable and cannot be scheduled.
- Reinstalling the same node preserves its active credential and reuses the Windows virtual
  environment and persistent pip cache, avoiding an avoidable outage and repeat downloads.
- Browser mode is optional. It is not ready until health confirms the browsers, interactive desktop,
  authenticated profile and evidence capabilities.

## Acceptance

- The flow works with keyboard input at 1440px, 768px and 390px without page-level overflow.
- A private key or node credential never appears in persisted metadata, logs or API output.
- A successful node appears in `/api/nodes` and is usable only for declared workloads.
- Project preflight fails when the bound node is missing, offline or lacks declared capabilities.
