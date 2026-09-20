from pathlib import Path
from typing import Any

from taskhub_v2.projects import ProjectProvisioner, ProjectProvisionError
from taskhub_v2.services.configuration_views import safe_error


def build_project_provisioner(settings, registry) -> ProjectProvisioner:
    ssh_directory = Path(settings.model_account_root).parent / "ssh"
    known_hosts = ssh_directory / "known_hosts"
    private_key_file = ""
    if settings.authority_git_private_key:
        ssh_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        key_path = ssh_directory / "authority_key"
        temporary = ssh_directory / "authority_key.tmp"
        temporary.write_text(settings.authority_git_private_key.strip() + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(key_path)
        private_key_file = str(key_path)
    return ProjectProvisioner(
        registry,
        settings.authority_git_host,
        settings.authority_git_root,
        settings.managed_repository_root,
        authority_port=settings.authority_git_port,
        private_key_file=private_key_file,
        known_hosts_file=str(known_hosts),
    )


async def test_git_repository_connection(
    settings,
    store,
    payload,
    private_key: str,
    operator: str,
) -> dict[str, Any]:
    values = payload.model_dump(exclude={"authority_git_private_key"})
    provisioner = ProjectProvisioner(
        None,
        values["authority_git_host"],
        values["authority_git_root"],
        values["managed_repository_root"],
        authority_port=values["authority_git_port"],
        private_key=payload.authority_git_private_key or private_key,
        known_hosts_file=str(Path(settings.model_account_root).parent / "ssh/known_hosts"),
    )
    try:
        result = await provisioner.test_connection()
    except (ProjectProvisionError, OSError, ValueError) as exc:
        result = {"available": False, "detail": safe_error(exc)}
    await store.add_audit(
        operator=operator,
        scope="platform",
        action="git_connection_test",
        parameter_summary={
            "authority_git_host": values["authority_git_host"],
            "authority_git_port": values["authority_git_port"],
            "authority_git_root": values["authority_git_root"],
        },
        result="passed" if result["available"] else "failed",
    )
    return result
