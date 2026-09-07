# 2026-09-07 TaskHub Node Sandbox Block

## Summary

TaskHub V2 run `de67eed5-84da-4453-b0c5-90da564792ac` for
`douyin-listing-workbench` stopped at `implementation_blocked`.

The persisted blocking reason was:

```json
{"code": "no_changes", "detail": "coding model produced no file changes"}
```

The user-visible `no_changes` state was a symptom. The underlying failure was
that Ubuntu execution nodes could start Codex and receive model output, but
Codex could not enter its `workspace-write` filesystem sandbox.

## Evidence

- Controller `192.168.31.51` was healthy and dispatched the coding job.
- Worker `192.168.31.52` accepted the workspace and returned from the code job.
- A minimal Git worktree edit on `192.168.31.52` failed with:
  `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`.
- `192.168.31.53` reproduced the same workspace-write failure.
- `192.168.31.54` did not have coding provider configuration.
- Normal node `192.168.31.31:8022` passed `unshare -Ur true` and the same
  Codex workspace-write edit.

## Cause

The TaskHub application and credentials can be copied between hosts, but Linux
kernel namespace, AppArmor, bubblewrap, and host security policy are machine
level prerequisites. Nodes `.52` and `.53` did not provide the user namespace
capability required by Codex workspace-write.

## Follow-up

- Add system configuration diagnostics to the TaskHub UI.
- Surface controller and node OS/runtime/component/preflight checks.
- Mark a node as non-coding when workspace-write prerequisites fail.
- Include this check in future install and clone admission procedures.
