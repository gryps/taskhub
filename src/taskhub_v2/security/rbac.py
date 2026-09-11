ROLE_PERMISSIONS = {
    "administrator": {"*"},
    "project_owner": {"read", "projects:manage", "delivery:execute", "release:manage"},
    "developer": {"read", "delivery:execute"},
    "auditor": {"read", "audit:read"},
}
