# TaskHub Integration Stage Completion

The eight integration tasks requested after the advanced stage are implemented.

1. Windows GUI node inventory: the Project Governance page reports generic node health and declared TaskHub capabilities.
2. Guided real workflow: an onboarded project can launch its first five-role workflow from one requirement field and an optional pipeline selection.
3. Git authority synchronization: TaskHub reads the allowlisted authority host over key-only SSH, records hash-versioned snapshots, and repeats at the configured interval. Commands are read-only.
4. Artifact exchange: workers upload bounded files to controller storage. Metadata, producer, task, size, media type, and SHA-256 are persisted and files can be downloaded from the UI.
5. Governed execution: allowed low-risk actions are now executed rather than only classified. Every execution and outcome is persisted; release, publish, credentials, deletion, and production apply remain human-only.
6. One-click onboarding: a four-step wizard performs source and worker preflight, then idempotently creates the project, context baseline, A/B pipelines, isolated workspaces, synchronization, budget, and governance policy in one transaction.
7. Device management: TaskHub discovers generic worker capabilities and health without probing or configuring unrelated business devices.
8. Cost and quota reporting: the dashboard groups calls, tokens, failures, and estimated cost by role/provider/model, includes daily totals and budget status, and states the Plus/Pro CLI quota visibility limitation.

## Safety boundaries

- Source hosts must be in `TASKHUB_PROJECT_SOURCE_HOSTS`.
- Source synchronization uses `BatchMode=yes`; TaskHub does not store SSH passwords.
- Artifact uploads default to 20 MiB maximum and require worker authentication.
- Business collection and listing systems are outside TaskHub scope and cannot be scheduled through TaskHub.
- Final release and publication always require a human decision.

## Configuration

```bash
TASKHUB_PROJECT_SOURCE_HOSTS=192.168.31.3
PROJECT_SOURCE_HOST=192.168.31.3
PROJECT_SOURCE_ROOT=/home/gryps/projects/douyin-listing-workbench
CODEX_PROJECT_WORKDIR=/home/gryps/apps/douyin-listing-workbench
PROJECT_SOURCE_SSH_USER=gryps
PROJECT_SOURCE_SSH_PORT=22
TASKHUB_ARTIFACT_ROOT=/home/gryps/apps/langgraph-control/data/artifacts
TASKHUB_ARTIFACT_MAX_BYTES=20971520
TASKHUB_INTEGRATION_POLL_SECONDS=60
```

The Windows worker exposes only generic H5 inspection. Existing collection and listing applications remain independent and are not configured, discovered, or invoked by TaskHub.
