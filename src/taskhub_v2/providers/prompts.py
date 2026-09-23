EVIDENCE_OWNERSHIP_POLICY = (
    "Respect the evidence provenance and execution_owner fields. Commands owned by "
    "TaskHub quality or acceptance workers are platform-configured verification, not "
    "actions performed by the coding worker. Do not use those commands to claim that "
    "the coding worker violated an implementation instruction such as not installing "
    "dependencies or not running bootstrap. Restrictions on the implementation actor "
    "apply only to coding-worker-owned actions and the resulting diff, unless the "
    "requirement explicitly says that the TaskHub platform itself must not execute that "
    "verification. Platform verification remains valid delivery evidence."
)

CODING_VERIFICATION_POLICY = (
    "Do not install or upgrade dependencies and do not run project bootstrap or the "
    "configured full quality or acceptance commands. TaskHub runs those commands in "
    "independent verification workers after coding. You may run focused checks that use "
    "dependencies already present in the worktree, and you must still add or update the "
    "tests required by the change."
)
