# Distributed Test And Build Execution

## Business Goal

Use the controller and one independent Linux node as two execution lanes without
moving model credentials or the authoritative Git repositories off the controller.

## Delivered

- JSON node registry with local and remote node definitions.
- Bearer-authenticated FastAPI Node Agent on port 8301.
- Safe tar extraction that rejects traversal, links, devices, and credential files.
- Workspace packaging that excludes Git metadata, credentials, dependencies, and builds.
- Runtime capability reporting for Git, Python, pytest, Node, and npm.
- Command-aware scheduling: a task is assigned only to a node that has every required tool.
- One configurable slot per node, sticky assignment per TaskHub run, health status,
  cooldown, and transport failover.
- Workload allowlists and numeric priorities keep primary lanes ahead of auxiliary
  nodes while preserving overflow and failure recovery.
- Implementation and publication tests use the same scheduler and record the node ID.
- Browser node status showing availability, slot use, and detected capabilities.

## Live Topology

| Host | Service | Port | Responsibility |
| --- | --- | --- | --- |
| `192.168.31.31:8022` | TaskHub V2 controller | 8200 | LangGraph, models, Git worktrees, primary A slot |
| `192.168.31.31:8023` | TaskHub V2 Node Agent | 8301 | Primary B coding/test/build slot |
| `192.168.31.24` | TaskHub V2 Node Agent | 8301 | Auxiliary test/build and failover slot |
| `192.168.31.24` | V1 LangGraph Worker | 8000 | Existing V1 service, unchanged |

Each V2 Agent uses an independent virtual environment copied from an existing local
environment. No package download was required and the source environments remain unchanged.

## Acceptance

1. An unauthenticated health or execution request returns 401.
2. An archive containing path traversal is rejected and writes nothing outside the job root.
3. pytest commands are not scheduled to a node without pytest; npm commands require npm.
4. A remote workspace can run pytest and npm in sequence with both exit codes equal to zero.
5. Node status is visible through `/api/nodes` and the browser console.
6. Existing V1 port 8000 remains active after the V2 Agent starts on 8301.

## Explicit Boundary

Coding-enabled nodes invoke their locally configured model profiles and return validated
change bundles. Git commits, review, rebase, and authority publication remain on the
controller. Auxiliary nodes without the `coding` workload never receive model tasks or
model credentials.
