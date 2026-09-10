import asyncio
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from taskhub_v2.api.app import create_app
from taskhub_v2.config import Settings
from taskhub_v2.domain.configuration import ModelServicesUpdate, PlatformSettingsUpdate
from taskhub_v2.persistence.configuration import MemoryConfigurationStore, configuration_store
from taskhub_v2.security.encryption import SecretCipher
from taskhub_v2.services.configuration import ManagedConfigurationService


def configured_settings(**overrides) -> Settings:
    values = {
        "checkpointer": "memory",
        "admin_token": "admin-secret",
        "session_secret": "session-secret",
        "config_encryption_key": Fernet.generate_key().decode(),
        **overrides,
    }
    return Settings(
        **values,
    )


def login(client: TestClient) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"token": "admin-secret"})
    assert response.status_code == 200
    return {"X-CSRF-Token": client.cookies["taskhub_v2_csrf"]}


def test_model_service_configuration_encrypts_and_masks_api_key():
    with TestClient(create_app(configured_settings())) as client:
        headers = login(client)
        response = client.put(
            "/api/settings/model-services",
            json={
                "provider": "deterministic",
                "gpt_base_url": "https://models.example.test/v1",
                "gpt_model": "gpt-test",
                "gpt_api_key": "managed-api-key-secret",
            },
            headers=headers,
        )
        assert response.status_code == 200
        body = response.text
        assert "managed-api-key-secret" not in body
        payload = response.json()
        assert payload["desired"]["gpt_model"] == "gpt-test"
        assert payload["secrets"]["gpt_api_key"] == {
            "configured": True,
            "mask": "mana********cret",
            "source": "managed",
        }
        assert payload["restart_required"] is True
        assert payload["version"] == 1
        assert payload["applied_version"] == 0

        audit = client.get("/api/settings/audit?scope=model_services").json()["events"]
        assert audit[0]["action"] == "update"
        assert audit[0]["parameter_summary"]["replaced_secrets"] == ["gpt_api_key"]
        assert "managed-api-key-secret" not in str(audit)


def test_model_service_secret_requires_encryption_key():
    settings = configured_settings().model_copy(update={"config_encryption_key": ""})
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        response = client.put(
            "/api/settings/model-services",
            json={"gpt_api_key": "must-not-be-written"},
            headers=headers,
        )
    assert response.status_code == 409
    assert "TASKHUB_CONFIG_ENCRYPTION_KEY" in response.json()["detail"]
    assert "must-not-be-written" not in response.text


def test_platform_configuration_is_validated_and_protects_bootstrap_fields():
    with TestClient(create_app(configured_settings())) as client:
        headers = login(client)
        invalid = client.put(
            "/api/settings/platform",
            json={"node_heartbeat_seconds": 60, "node_offline_seconds": 30},
            headers=headers,
        )
        assert invalid.status_code == 409

        response = client.put(
            "/api/settings/platform",
            json={
                "seed_public_url": "http://192.168.31.31:8200",
                "node_callback_url": "http://192.168.31.31:8200",
                "node_container_image": "taskhub-node:0.2.0-alpha",
                "default_node_slots": 2,
                "node_heartbeat_seconds": 15,
                "node_offline_seconds": 60,
            },
            headers=headers,
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["desired"]["node_container_image"] == "taskhub-node:0.2.0-alpha"
        assert payload["effective"]["node_container_image"] != "taskhub-node:0.2.0-alpha"
        assert payload["restart_required"] is True
        assert payload["protected_bootstrap"] == [
            "postgres_dsn",
            "session_secret",
            "config_encryption_key",
            "node_token",
        ]
        assert "session-secret" not in response.text


def test_configuration_mutations_require_authentication_and_csrf():
    with TestClient(create_app(configured_settings())) as client:
        payload = {"provider": "deterministic"}
        assert client.put("/api/settings/model-services", json=payload).status_code == 401
        login(client)
        assert client.put("/api/settings/model-services", json=payload).status_code == 403


def test_connection_test_is_audited_without_external_network():
    with TestClient(create_app(configured_settings())) as client:
        headers = login(client)
        response = client.post(
            "/api/settings/model-services/test",
            json={"provider_id": "deterministic"},
            headers=headers,
        )
        assert response.json() == {
            "provider_id": "deterministic",
            "available": True,
            "detail": "内置确定性模型无需外部连接",
        }
        audit = client.get("/api/settings/audit").json()["events"]
        assert audit[0]["action"] == "connection_test"
        assert audit[0]["result"] == "passed"


def test_saved_configuration_applies_on_next_startup():
    async def scenario():
        key = Fernet.generate_key().decode()
        settings = configured_settings(config_encryption_key=key)
        store = MemoryConfigurationStore()
        first = ManagedConfigurationService(settings, store, SecretCipher(key))
        await first.update_model_services(
            ModelServicesUpdate(
                provider="deterministic",
                gpt_model="gpt-managed",
                gpt_api_key="encrypted-secret-value",
            )
        )
        await first.update_platform_settings(
            PlatformSettingsUpdate(node_container_image="taskhub-node:managed")
        )

        second = ManagedConfigurationService(settings, store, SecretCipher(key))
        effective = await second.apply()
        await second.mark_applied()
        assert effective.gpt_model == "gpt-managed"
        assert effective.gpt_api_key == "encrypted-secret-value"
        assert effective.node_container_image == "taskhub-node:managed"
        assert (await second.model_services())["restart_required"] is False
        assert (await second.platform_settings())["restart_required"] is False

    asyncio.run(scenario())


def test_seed_package_generates_and_injects_configuration_encryption_key():
    root = Path(__file__).parents[1] / "deploy" / "seed"
    script = (root / "start-seed.ps1").read_text(encoding="utf-8")
    compose = (root / "compose.yaml").read_text(encoding="utf-8")
    example = (root / ".env.example").read_text(encoding="utf-8")

    assert "function New-FernetKey" in script
    assert "TASKHUB_CONFIG_ENCRYPTION_KEY=$(New-FernetKey)" in script
    assert "TASKHUB_CONFIG_ENCRYPTION_KEY:" in compose
    assert "TASKHUB_CONFIG_ENCRYPTION_KEY=replace-with-fernet-key" in example


def test_postgres_configuration_and_audit_are_durable(postgres_dsn):
    async def scenario():
        settings = configured_settings(checkpointer="postgres", postgres_dsn=postgres_dsn)
        async with configuration_store(settings) as store:
            saved = await store.save("platform", {"default_node_slots": 3}, {})
            await store.add_audit(
                operator="admin",
                scope="platform",
                action="update",
                parameter_summary={"changed_fields": ["default_node_slots"]},
                result="pending_restart",
            )
            assert saved.version == 1
        async with configuration_store(settings) as store:
            loaded = await store.get("platform")
            events = await store.list_audit("platform")
            assert loaded is not None
            assert loaded.payload == {"default_node_slots": 3}
            assert events[0]["parameter_summary"] == {
                "changed_fields": ["default_node_slots"]
            }

    asyncio.run(scenario())
