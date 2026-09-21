from types import SimpleNamespace

from taskhub_v2.config import Settings
from taskhub_v2.services.production_readiness import production_readiness


def test_formal_settings_pass_production_readiness_contract():
    report = production_readiness(
        Settings(
            checkpointer="postgres",
            production_orchestration_enabled=True,
            enforce_https=True,
            cookie_secure=True,
            session_state_file="/data/sessions.json",
            session_signing_keys_file="/data/signing-keys.json",
            operations_log_file="/data/operations.jsonl",
            config_encryption_key="configured-for-test",
            docker_socket="http://docker-proxy:2375",
            container_provisioning_enabled=True,
        ),
        SimpleNamespace(container_manager=object()),
    )
    assert report["ready"] is True
    assert report["summary"] == {"passed": 8, "total": 8}


def test_development_defaults_explain_failed_production_controls():
    report = production_readiness(Settings(), SimpleNamespace())
    assert report["ready"] is False
    failed = {item["id"]: item for item in report["checks"] if not item["passed"]}
    assert {
        "persistent_orchestration",
        "https_session",
        "docker_isolation",
        "local_node_management",
    } <= failed.keys()
    assert all(item["remediation"] for item in failed.values())
