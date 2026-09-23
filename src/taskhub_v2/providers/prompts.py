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
