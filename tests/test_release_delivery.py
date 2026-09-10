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
    }
    assert expected.issubset({path.name for path in RELEASE.iterdir()})


def test_windows_scripts_are_powershell_51_safe():
    for path in RELEASE.glob("*.ps1"):
        payload = path.read_bytes()
        assert payload.startswith(b"\xef\xbb\xbf")
        text = payload.decode("utf-8-sig")
        assert '$ErrorActionPreference = "Continue"' in text
        assert '$PSDefaultParameterValues["*:ErrorAction"] = "Stop"' in text


def test_release_compose_is_immutable_and_keeps_postgres_private():
    payload = yaml.safe_load((RELEASE / "compose.yaml").read_text(encoding="utf-8"))
    controller = payload["services"]["controller"]
    postgres = payload["services"]["postgres"]

    assert "build" not in controller
    assert all("/opt/taskhub/src" not in item for item in controller["volumes"])
    assert controller["ports"] == ["${TASKHUB_PORT:-8200}:8200"]
    assert "ports" not in postgres
    assert payload["volumes"]["taskhub-data"]["name"] == "${TASKHUB_DATA_VOLUME:-taskhub-data}"
    assert payload["volumes"]["postgres-data"]["name"] == (
        "${TASKHUB_POSTGRES_VOLUME:-taskhub-postgres-data}"
    )


def test_release_compose_preloads_unified_node_reference():
    text = (RELEASE / "compose.yaml").read_text(encoding="utf-8")
    assert "TASKHUB_NODE_CONTAINER_IMAGE: ${TASKHUB_NODE_IMAGE" in text
    assert "TASKHUB_CODEX_CLI_BIN: /usr/local/bin/codex" in text
    assert "/var/run/docker.sock:/var/run/docker.sock" in text
    assert "TASKHUB_OPERATIONS_LOG_FILE: /var/lib/taskhub/state/operations.jsonl" in text


def test_backup_restore_binds_encryption_key_to_database_identity():
    for name in ("backup.sh", "backup.ps1", "restore.sh", "restore.ps1"):
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
        assert "pg_restore -a -t taskhub_backup_identity" in text
        assert text.index("pg_restore -a -t taskhub_backup_identity") < text.index(" down")
        assert "pg_restore -a -t taskhub_backup_identity" in text


def test_release_images_pin_codex_and_node_has_common_role_tools():
    seed = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    node = (ROOT / "deploy" / "node" / "Dockerfile").read_text(encoding="utf-8")

    assert "ARG CODEX_VERSION=" in seed
    assert "CODEX_RELEASE=\"${CODEX_VERSION}\"" in seed
    assert "TASKHUB_CODEX_CLI_BIN=/usr/local/bin/codex" in seed
    assert "ARG DEBIAN_MIRROR=" in seed
    assert "ARG DEBIAN_SECURITY_MIRROR=" in seed
    assert "git nodejs npm" in node
    assert "ARG DEBIAN_MIRROR=" in node
    assert "ARG DEBIAN_SECURITY_MIRROR=" in node
    assert "python -m pip install '.[dev,browser]'" in node

    for name in ("build-images.sh", "build-images.ps1"):
        build_script = (RELEASE / name).read_text(encoding="utf-8-sig")
        assert "TASKHUB_COMMIT" in build_script
        assert "DEBIAN_MIRROR" in build_script
        assert "DEBIAN_SECURITY_MIRROR" in build_script


def test_offline_build_includes_all_runtime_images_and_checksums():
    for name in ("build-offline.sh", "build-offline.ps1"):
        text = (RELEASE / name).read_text(encoding="utf-8")
        assert "taskhub-seed" in text
        assert "taskhub-node" in text
        assert "postgres:16-alpine" in text
        assert "SHA256SUMS" in text
        assert "manifest.json" in text
        assert "Get-ChildItem" in text or "find . -type f" in text


def test_initializers_never_request_sudo_credentials():
    for name in ("init.sh", "init.ps1"):
        text = (RELEASE / name).read_text(encoding="utf-8").lower()
        assert "sudo -s" not in text
        assert "sudo_password" not in text
        assert "docker info" in text


def test_release_kit_has_no_site_specific_paths_or_addresses():
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in RELEASE.iterdir()
        if path.is_file()
    )
    assert "192.168.31." not in text
    assert "/Users/gryps" not in text
    assert "C:\\taskhub-seed" not in text
