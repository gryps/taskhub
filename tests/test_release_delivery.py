import re
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
RELEASE = ROOT / "deploy" / "release"


def test_release_kit_contains_cross_platform_lifecycle_assets():
    expected = {
        ".env.example",
        "README.md",
        "compose.yaml",
        "init.sh",
        "init.ps1",
        "build-images.sh",
        "build-images.ps1",
        "build-offline.sh",
        "build-offline.ps1",
        "backup.sh",
        "backup.ps1",
        "upgrade.sh",
        "upgrade.ps1",
        "restore.sh",
        "restore.ps1",
        "verify-backup.sh",
        "verify-backup.ps1",
        "prepare-ubuntu.sh",
        "prepare-windows.ps1",
        "preflight.sh",
        "preflight.ps1",
        "verify.sh",
        "verify.ps1",
        "configure-tls.sh",
        "configure-tls.ps1",
        "package-online.sh",
        "package-online.ps1",
    }
    assert expected.issubset({path.name for path in RELEASE.iterdir()})


def test_windows_scripts_are_powershell_51_safe():
    for path in RELEASE.glob("*.ps1"):
        payload = path.read_bytes()
        assert payload.startswith(b"\xef\xbb\xbf")
        text = payload.decode("utf-8-sig")
        assert '$ErrorActionPreference = "Continue"' in text
        assert '$PSDefaultParameterValues["*:ErrorAction"] = "Stop"' in text


def test_release_image_builds_forward_optional_outbound_proxy():
    shell = (RELEASE / "build-images.sh").read_text(encoding="utf-8")
    powershell = (RELEASE / "build-images.ps1").read_text(encoding="utf-8-sig")

    for script in (shell, powershell):
        assert "TASKHUB_BUILD_PROXY" in script
        assert "HTTP_PROXY" in script
        assert "HTTPS_PROXY" in script


def test_release_compose_is_immutable_and_keeps_postgres_private():
    payload = yaml.safe_load((RELEASE / "compose.yaml").read_text(encoding="utf-8"))
    controller = payload["services"]["controller"]
    postgres = payload["services"]["postgres"]

    assert "build" not in controller
    assert all("/opt/taskhub/src" not in item for item in controller["volumes"])
    assert controller["ports"] == [
        "${TASKHUB_PORT:-8200}:8200",
        "${TASKHUB_PREVIEW_BIND_ADDRESS:-0.0.0.0}:8400-8499:8400-8499",
    ]
    assert "ports" not in postgres
    assert payload["volumes"]["taskhub-data"]["name"] == "${TASKHUB_DATA_VOLUME:-taskhub-data}"
    assert payload["volumes"]["postgres-data"]["name"] == (
        "${TASKHUB_POSTGRES_VOLUME:-taskhub-postgres-data}"
    )


def test_release_compose_preloads_unified_node_reference():
    text = (RELEASE / "compose.yaml").read_text(encoding="utf-8")
    assert "TASKHUB_NODE_CONTAINER_IMAGE: ${TASKHUB_NODE_IMAGE" in text
    assert "TASKHUB_CODEX_CLI_BIN: /usr/local/bin/codex" in text
    payload = yaml.safe_load(text)
    controller = payload["services"]["controller"]
    proxy = payload["services"]["docker-proxy"]
    assert "/var/run/docker.sock:/var/run/docker.sock" not in str(controller)
    assert controller["environment"]["TASKHUB_DOCKER_SOCKET"] == "http://docker-proxy:2375"
    assert controller["environment"]["TASKHUB_DOCKER_NETWORK"] == (
        "${TASKHUB_DOCKER_NETWORK:-taskhub_default}"
    )
    assert controller["environment"]["TASKHUB_WORKER_MODE"] == (
        "${TASKHUB_WORKER_MODE:-git}"
    )
    assert controller["environment"]["TASKHUB_DATA_VOLUME_NAME"] == (
        "${TASKHUB_DATA_VOLUME:-taskhub-data}"
    )
    assert controller["environment"]["TASKHUB_MODEL_ACCOUNTS_VOLUME_SUBPATH"] == (
        "config/model-accounts/node-runtime"
    )
    assert "/var/run/docker.sock:/var/run/docker.sock:ro" in proxy["volumes"]
    assert "ports" not in proxy
    assert proxy["environment"]["AUTH"] == 0
    assert proxy["environment"]["SECRETS"] == 0
    assert "TASKHUB_OPERATIONS_LOG_FILE: /var/lib/taskhub/state/operations.jsonl" in text
    assert "TASKHUB_COOKIE_SECURE: ${TASKHUB_COOKIE_SECURE:-true}" in text
    assert "TASKHUB_SESSION_STATE_FILE: /var/lib/taskhub/state/sessions.json" in text


def test_unified_node_uses_official_node_22_runtime():
    dockerfile = (ROOT / "deploy" / "node" / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM node:22-bookworm-slim AS node-runtime" in dockerfile
    assert "COPY --from=node-runtime /usr/local/ /usr/local/" in dockerfile
    assert "apt-get install" in dockerfile
    assert " nodejs npm " not in dockerfile
    assert "node --version" in dockerfile


def test_initializers_prefer_active_legacy_volumes_over_empty_standard_names():
    shell = (RELEASE / "init.sh").read_text(encoding="utf-8-sig")
    powershell = (RELEASE / "init.ps1").read_text(encoding="utf-8-sig")

    assert "docker ps -aq --filter volume=taskhub-seed_taskhub-data" in shell
    assert "docker ps -aq --filter volume=taskhub-seed_postgres-data" in shell
    assert 'docker ps -aq --filter "volume=taskhub-seed_taskhub-data"' in powershell
    assert 'docker ps -aq --filter "volume=taskhub-seed_postgres-data"' in powershell


def test_backup_restore_binds_encryption_key_to_database_identity():
    for name in (
        "backup.sh", "backup.ps1", "restore.sh", "restore.ps1",
        "verify-backup.sh", "verify-backup.ps1",
    ):
        text = (RELEASE / name).read_text(encoding="utf-8-sig")
        assert "TASKHUB_CONFIG_KEY_FINGERPRINT" in text
        assert "taskhub-backup-v1:" in text
    for name in ("restore.sh", "restore.ps1"):
        text = (RELEASE / name).read_text(encoding="utf-8-sig")
        assert "taskhub_backup_identity" in text
        assert "pg_restore -a -t taskhub_backup_identity" in text
        assert text.index("pg_restore -a -t taskhub_backup_identity") < text.index(
            "docker compose --project-directory"
        )
    assert ') -join "`n"' in (RELEASE / "restore.ps1").read_text(encoding="utf-8-sig")
    assert "verify-backup.sh" in (RELEASE / "backup.sh").read_text(encoding="utf-8")
    assert "verify-backup.ps1" in (RELEASE / "backup.ps1").read_text(encoding="utf-8-sig")


def test_release_images_pin_codex_and_node_has_common_role_tools():
    seed = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    node = (ROOT / "deploy" / "node" / "Dockerfile").read_text(encoding="utf-8")

    assert "ARG CODEX_VERSION=" in seed
    assert "CODEX_RELEASE=\"${CODEX_VERSION}\"" in seed
    assert "TASKHUB_CODEX_CLI_BIN=/usr/local/bin/codex" in seed
    assert "ARG NPM_REGISTRY=" in seed
    assert "npm config set registry" in seed
    assert "ARG DEBIAN_MIRROR=" in seed
    assert "ARG DEBIAN_SECURITY_MIRROR=" in seed
    assert "HEALTHCHECK" in seed and "  CMD if [ -n" in seed
    assert "ca-certificates curl git openssh-client openssl" in seed
    assert "FROM node:22-bookworm-slim AS node-runtime" in node
    assert "ca-certificates curl git openssh-client" in node
    assert "ARG DEBIAN_MIRROR=" in node
    assert "ARG DEBIAN_SECURITY_MIRROR=" in node
    assert "--mount=type=cache,id=taskhub-pip" in seed
    assert "python -m pip install --no-build-isolation ." in seed
    assert "python -m pip install --no-deps --no-build-isolation --force-reinstall ." in seed
    assert "--mount=type=cache,id=taskhub-pip" in node
    assert "python -m pip install --no-build-isolation '.[dev,browser]'" in node
    assert "python -m pip install --no-deps --no-build-isolation --force-reinstall ." in node
    assert "python -m pip install --no-cache-dir" not in node
    assert "TASKHUB_NODE_CACHE_ROOT=/var/lib/taskhub-node/cache" in node
    assert "PIP_NO_CACHE_DIR=1" not in node
    for dockerfile in (seed, node):
        # Third-party dependencies are installed from a minimal package tree
        # before application sources are copied. Source-only edits therefore
        # rebuild only the final no-dependency package layer.
        dependency_install = dockerfile.index("python -m pip install --no-build-isolation")
        assert dependency_install < dockerfile.index("COPY src ./src")
        assert dockerfile.index("COPY src ./src") < dockerfile.index(
            "python -m pip install --no-deps --no-build-isolation --force-reinstall ."
        )
        # Per-release metadata must not invalidate the expensive OS, Codex,
        # and Python dependency layers on every source-only rebuild.
        assert dockerfile.index("ARG TASKHUB_COMMIT=") > dockerfile.index(
            "python -m pip install"
        )

    for name in ("build-images.sh", "build-images.ps1"):
        build_script = (RELEASE / name).read_text(encoding="utf-8-sig")
        assert "TASKHUB_COMMIT" in build_script
        assert "NPM_REGISTRY" in build_script
        assert "--provenance=false" in build_script
        assert "DEBIAN_MIRROR" in build_script
        assert "DEBIAN_SECURITY_MIRROR" in build_script


def test_offline_build_includes_all_runtime_images_and_checksums():
    for name in ("build-offline.sh", "build-offline.ps1"):
        text = (RELEASE / name).read_text(encoding="utf-8")
        assert "taskhub-seed" in text
        assert "taskhub-node" in text
        assert "postgres:16-alpine" in text
        assert "ghcr.io/tecnativa/docker-socket-proxy:v0.5.0" in text
        assert "SHA256SUMS" in text
        assert "manifest.json" in text
        assert "Get-ChildItem" in text or "find . -type f" in text


def test_initializers_never_request_sudo_credentials():
    for name in ("init.sh", "init.ps1"):
        text = (RELEASE / name).read_text(encoding="utf-8").lower()
        assert "sudo -s" not in text
        assert "sudo_password" not in text
        assert "docker info" in text


def test_host_preparation_requires_explicit_install_opt_in():
    ubuntu = (RELEASE / "prepare-ubuntu.sh").read_text(encoding="utf-8")
    windows = (RELEASE / "prepare-windows.ps1").read_text(encoding="utf-8-sig")
    assert "--install-docker" in ubuntu
    assert "download.docker.com/linux/ubuntu" in ubuntu
    assert "sudo apt-get install" in ubuntu
    assert "-InstallDockerDesktop" in windows
    assert "Docker.DockerDesktop" in windows
    assert "wsl.exe --status" in windows


def test_preflight_and_verification_cover_images_runtime_and_nodes():
    for name in ("preflight.sh", "preflight.ps1"):
        text = (RELEASE / name).read_text(encoding="utf-8-sig")
        assert "TASKHUB_SEED_IMAGE" in text
        assert "TASKHUB_NODE_IMAGE" in text
        assert "TASKHUB_DOCKER_PROXY_IMAGE" in text
        assert "manifest inspect" in text
    for name in ("verify.sh", "verify.ps1"):
        text = (RELEASE / name).read_text(encoding="utf-8-sig")
        assert "taskhub-controller-1" in text
        assert "taskhub-postgres-1" in text
        assert "io.taskhub.managed=true" in text
        assert "TASKHUB_NODE_CODING_ENABLED" in text


def test_online_packagers_include_checksums_and_exclude_runtime_secrets():
    for name in ("package-online.sh", "package-online.ps1"):
        text = (RELEASE / name).read_text(encoding="utf-8-sig")
        assert "SHA256SUMS" in text
        assert ".env.example" in text
        assert "ubuntu.md" in text
        assert "windows-docker-desktop.md" in text
        assert "images.tar" not in text
        assert "backups" not in text


def test_tls_tools_support_generated_and_imported_certificates():
    shell = (RELEASE / "configure-tls.sh").read_text(encoding="utf-8")
    powershell = (RELEASE / "configure-tls.ps1").read_text(encoding="utf-8-sig")
    for text in (shell, powershell):
        assert "subjectAltName" in text
        assert "taskhub.crt" in text
        assert "taskhub.key" in text
    assert "--cert" in shell and "--key" in shell
    assert "Certificate" in powershell and "PrivateKey" in powershell


def test_online_initializers_preload_seed_and_node_images():
    shell = (RELEASE / "init.sh").read_text(encoding="utf-8")
    shell_online = shell.split("else\n  for image in \\", 1)[1].split(
        "fi\nverify_image_platforms", 1
    )[0]
    assert "TASKHUB_NODE_IMAGE" in shell_online

    powershell = (RELEASE / "init.ps1").read_text(encoding="utf-8-sig")
    powershell_online = powershell.split("} else {\n    foreach ($Image in @(", 1)[1].split(
        "    )) {", 1
    )[0]
    assert "TASKHUB_NODE_IMAGE" in powershell_online

    env_example = (RELEASE / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^TASKHUB_SEED_IMAGE=ghcr\.io/gryps/taskhub-seed:", env_example, re.M)
    assert re.search(r"^TASKHUB_NODE_IMAGE=ghcr\.io/gryps/taskhub-node:", env_example, re.M)

    assert 'put_env_value TASKHUB_NODE_IMAGE "taskhub-node:$offline_version"' in shell
    assert 'Set-EnvValue "TASKHUB_NODE_IMAGE" "taskhub-node:$OfflineVersion"' in powershell


def test_release_kit_has_no_site_specific_paths_or_addresses():
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in RELEASE.iterdir()
        if path.is_file()
    )
    assert "192.168.31." not in text
    assert "/Users/gryps" not in text
    assert "C:\\taskhub-seed" not in text
