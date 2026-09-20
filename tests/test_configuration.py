import asyncio
from pathlib import Path

import httpx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from taskhub_v2.api.app import create_app
from taskhub_v2.config import Settings
from taskhub_v2.domain.configuration import ModelServicesUpdate, PlatformSettingsUpdate
from taskhub_v2.persistence.configuration import MemoryConfigurationStore, configuration_store
from taskhub_v2.security.encryption import SecretCipher
from taskhub_v2.services.configuration import ManagedConfigurationService
from taskhub_v2.services.node_model_config import write_node_model_configuration
from taskhub_v2.workers.factory import build_coder


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


def test_api_model_card_secret_is_saved_encrypted_without_server_error():
    with TestClient(create_app(configured_settings())) as client:
        headers = login(client)
        response = client.put(
            "/api/settings/model-services",
            json={
                "model_cards": [
                    {
                        "model_id": "minimax-card",
                        "display_name": "MiniMax",
                        "service_type": "minimax",
                        "auth_mode": "api",
                        "base_url": "https://api.minimax.example/v1",
                        "model": "MiniMax-M2.1",
                        "proxy_url": "",
                        "enabled": False,
                        "assignments": [],
                        "api_key": "minimax-secret-value",
                    }
                ]
            },
            headers=headers,
        )

        assert response.status_code == 200
        assert "minimax-secret-value" not in response.text
        payload = response.json()
        assert payload["card_credentials"]["minimax-card"] == {
            "configured": True,
            "mask": "mini********alue",
            "kind": "api",
        }
        assert "api_key" not in payload["desired"]["model_cards"][0]


def test_unsaved_model_card_connection_test_returns_controlled_response():
    with TestClient(create_app(configured_settings())) as client:
        headers = login(client)
        response = client.post(
            "/api/settings/model-services/test",
            json={"provider_id": "minimax-card"},
            headers=headers,
        )

        assert response.status_code == 200
        assert response.json() == {
            "provider_id": "minimax-card",
            "available": False,
            "detail": "模型卡片尚未保存，请先保存配置后再测试连接",
            "models": [],
        }


def test_unsaved_api_card_can_read_models_before_model_is_selected(monkeypatch):
    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **options):
            assert url == "https://models.example.test/v1/models"
            assert options["headers"] == {"Authorization": "Bearer draft-secret"}
            return httpx.Response(200, json={"data": [{"id": "custom-model"}]})

    monkeypatch.setattr(
        "taskhub_v2.services.model_catalog.httpx.AsyncClient", lambda **_options: FakeClient()
    )
    with TestClient(create_app(configured_settings())) as client:
        headers = login(client)
        response = client.post(
            "/api/settings/model-services/test",
            json={
                "provider_id": "minimax-draft",
                "draft": {
                    "model_id": "minimax-draft",
                    "service_type": "custom",
                    "auth_mode": "api",
                    "base_url": "https://models.example.test/v1",
                    "proxy_url": "",
                    "api_key": "draft-secret",
                },
            },
            headers=headers,
        )

        assert response.status_code == 200
        assert "draft-secret" not in response.text
        assert response.json()["models"] == [
            {"id": "custom-model", "name": "custom-model"}
        ]
        assert client.get("/api/settings/model-services").json()["desired"]["model_cards"] == []


def test_unsaved_account_card_can_start_device_authorization():
    class FakeDeviceAuth:
        async def start(self, model_id, proxy_url=""):
            assert model_id == "account-draft"
            assert proxy_url == "http://proxy.example.test:7893"
            return {"model_id": model_id, "session_id": "device-session", "status": "waiting"}

    app = create_app(configured_settings())
    with TestClient(app) as client:
        app.state.device_auth = FakeDeviceAuth()
        headers = login(client)
        response = client.post(
            "/api/settings/model-services/device-auth",
            json={
                "model_id": "account-draft",
                "draft": {
                    "model_id": "account-draft",
                    "service_type": "openai",
                    "auth_mode": "account",
                    "base_url": "https://api.openai.com/v1",
                    "proxy_url": "http://proxy.example.test:7893",
                },
            },
            headers=headers,
        )

        assert response.status_code == 200
        assert response.json()["session_id"] == "device-session"
        assert client.get("/api/settings/model-services").json()["desired"]["model_cards"] == []


def test_minimax_adapter_uses_provider_auth_endpoint_and_catalog(monkeypatch):
    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **options):
            assert url == "https://www.minimaxi.com/v1/token_plan/remains"
            assert options["headers"]["Authorization"] == "Bearer sk-cp-valid"
            return httpx.Response(200, json={"base_resp": {"status_code": 0}})

    monkeypatch.setattr(
        "taskhub_v2.services.model_catalog.httpx.AsyncClient", lambda **_options: FakeClient()
    )
    service = ManagedConfigurationService(
        configured_settings(), MemoryConfigurationStore(), None
    )
    result = asyncio.run(
        service._test_model_card(
            {
                "model_id": "minimax-card",
                "service_type": "minimax",
                "auth_mode": "api",
                "base_url": "https://api.minimax.cn/v1",
                "proxy_url": "",
            },
            {"model_card__minimax-card": "sk-cp-valid"},
        )
    )

    assert result["available"] is True
    assert [item["id"] for item in result["models"]] == [
        "MiniMax-M3",
        "MiniMax-M2.7",
        "MiniMax-M2.7-highspeed",
    ]


def test_model_card_test_rejects_api_key_with_whitespace():
    service = ManagedConfigurationService(
        configured_settings(), MemoryConfigurationStore(), None
    )
    result = asyncio.run(
        service._test_model_card(
            {
                "model_id": "minimax-card",
                "service_type": "minimax",
                "auth_mode": "api",
                "base_url": "https://api.minimax.cn/v1",
                "proxy_url": "",
            },
            {"model_card__minimax-card": "API Key copied together"},
        )
    )

    assert result == {
        "available": False,
        "detail": "API Key 格式异常：不能包含空格或换行，请重新复制纯 Key",
        "models": [],
    }


def test_two_enabled_account_cards_require_and_accept_complete_role_routes():
    base_cards = [
        {
            "model_id": "planning-account",
            "display_name": "规划账号",
            "service_type": "openai",
            "auth_mode": "account",
            "base_url": "https://api.openai.com/v1",
            "model": "",
            "proxy_url": "",
            "enabled": True,
            "assignments": [{"role": "planner", "priority": 0}],
        },
        {
            "model_id": "coding-account",
            "display_name": "编码账号",
            "service_type": "openai",
            "auth_mode": "account",
            "base_url": "https://api.openai.com/v1",
            "model": "",
            "proxy_url": "",
            "enabled": True,
            "assignments": [{"role": "coder", "priority": 0}],
        },
    ]
    with TestClient(create_app(configured_settings())) as client:
        headers = login(client)
        incomplete = client.put(
            "/api/settings/model-services", json={"model_cards": base_cards}, headers=headers
        )
        assert incomplete.status_code == 422
        assert "已启用模型缺少supervisor主模型" in incomplete.text

        complete_cards = [dict(card) for card in base_cards]
        complete_cards[0]["assignments"] = [
            {"role": role, "priority": 0}
            for role in ("planner", "supervisor", "reviewer", "risk")
        ]
        saved = client.put(
            "/api/settings/model-services",
            json={"model_cards": complete_cards},
            headers=headers,
        )
        assert saved.status_code == 200
        assert [card["enabled"] for card in saved.json()["desired"]["model_cards"]] == [
            True,
            True,
        ]


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


def test_platform_registry_password_is_encrypted_and_never_returned():
    with TestClient(create_app(configured_settings())) as client:
        headers = login(client)
        response = client.put(
            "/api/settings/platform",
            json={
                "node_image_registry": "registry.example/team",
                "node_registry_username": "taskhub-puller",
                "node_registry_password": "private-registry-secret",
            },
            headers=headers,
        )
        assert response.status_code == 200
        assert "private-registry-secret" not in response.text
        payload = response.json()
        assert payload["desired"]["node_registry_username"] == "taskhub-puller"
        assert payload["secrets"]["node_registry_password"] == {
            "configured": True,
            "mask": "priv********cret",
            "source": "managed",
        }
        audit = client.get("/api/settings/audit?scope=platform").json()["events"]
        assert audit[0]["parameter_summary"]["replaced_secrets"] == [
            "node_registry_password"
        ]
        assert "private-registry-secret" not in str(audit)


def test_git_repository_service_encrypts_key_and_tests_unsaved_values(monkeypatch):
    private_key = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "test-private-material\n"
        "-----END OPENSSH PRIVATE KEY-----"
    )
    observed = {}

    async def fake_test(provisioner):
        observed.update(
            host=provisioner.authority_host,
            port=provisioner.authority_port,
            root=str(provisioner.authority_root),
            key=provisioner.private_key,
        )
        return {"available": True, "detail": "连接成功"}

    monkeypatch.setattr(
        "taskhub_v2.projects.ProjectProvisioner.test_connection", fake_test
    )
    with TestClient(create_app(configured_settings())) as client:
        headers = login(client)
        saved = client.put(
            "/api/settings/platform",
            json={
                "authority_git_host": "git@192.168.31.20",
                "authority_git_port": 2222,
                "authority_git_root": "/srv/git",
                "managed_repository_root": "/var/lib/taskhub/repositories",
                "authority_git_private_key": private_key,
            },
            headers=headers,
        )
        assert saved.status_code == 200
        assert private_key not in saved.text
        assert saved.json()["secrets"]["authority_git_private_key"]["configured"] is True

        tested = client.post(
            "/api/settings/platform/git-test",
            json={
                "authority_git_host": "git@git.example.test",
                "authority_git_port": 2202,
                "authority_git_root": "/repositories",
                "managed_repository_root": "/var/lib/taskhub/repositories",
            },
            headers=headers,
        )
        assert tested.json() == {"available": True, "detail": "连接成功"}
        assert observed == {
            "host": "git@git.example.test",
            "port": 2202,
            "root": "/repositories",
            "key": private_key,
        }
        audit = client.get("/api/settings/audit?scope=platform").json()["events"]
        assert audit[0]["action"] == "git_connection_test"
        assert private_key not in str(audit)


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


def test_api_model_card_test_returns_normalized_model_catalog(monkeypatch):
    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "model-z"},
                        {"id": "model-a", "name": "Model A"},
                        {"id": "model-a", "name": "Model A duplicate"},
                    ]
                },
            )

    monkeypatch.setattr(
        "taskhub_v2.services.model_catalog.httpx.AsyncClient", lambda **_options: FakeClient()
    )
    service = ManagedConfigurationService(
        configured_settings(), MemoryConfigurationStore(), None
    )
    result = asyncio.run(
        service._test_model_card(
            {
                "model_id": "api-card",
                "auth_mode": "api",
                "base_url": "https://models.example.test/v1",
                "proxy_url": "",
            },
            {"model_card__api-card": "secret"},
        )
    )

    assert result == {
        "available": True,
        "detail": "连接成功，读取到 2 个模型",
        "models": [
            {"id": "model-a", "name": "Model A duplicate"},
            {"id": "model-z", "name": "model-z"},
        ],
    }


def test_account_model_card_uses_its_codex_home_to_list_visible_models(tmp_path: Path):
    executable = tmp_path / "codex-models"
    executable.write_text(
        "#!/bin/sh\n"
        'test "$1" = "debug" || exit 2\n'
        'test "$2" = "models" || exit 3\n'
        'test "$CODEX_HOME" = "' + str(tmp_path / "accounts" / "account-card") + '" || exit 4\n'
        "printf '%s' '"
        '{"models":[{"slug":"gpt-visible","display_name":"GPT Visible",'
        '"visibility":"list"},{"slug":"gpt-hidden","visibility":"hide"}]}'
        "'\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    account_home = tmp_path / "accounts" / "account-card"
    account_home.mkdir(parents=True)
    (account_home / "auth.json").write_text("{}", encoding="utf-8")
    service = ManagedConfigurationService(
        configured_settings(
            codex_cli_bin=str(executable),
            model_account_root=str(tmp_path / "accounts"),
            openai_proxy_url="http://proxy.example.test:7893",
        ),
        MemoryConfigurationStore(),
        None,
    )

    result = asyncio.run(
        service._test_model_card(
            {"model_id": "account-card", "auth_mode": "account", "proxy_url": ""}, {}
        )
    )

    assert result == {
        "available": True,
        "detail": "ChatGPT 账号可用，读取到 1 个模型",
        "models": [{"id": "gpt-visible", "name": "GPT Visible"}],
    }


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


def test_node_model_configuration_exposes_only_coder_credentials(tmp_path):
    account_root = tmp_path / "accounts"
    for model_id in ("coder", "planner"):
        target = account_root / model_id
        target.mkdir(parents=True)
        (target / "auth.json").write_text(f'{{"account":"{model_id}"}}', encoding="utf-8")
    settings = configured_settings(
        data_volume_name="taskhub-data",
        model_accounts_volume_subpath="config/model-accounts/node-runtime",
        model_account_root=str(account_root),
        model_cards=[
            {
                "model_id": "coder",
                "service_type": "openai",
                "auth_mode": "account",
                "enabled": True,
                "assignments": [{"role": "coder", "priority": 0}],
            },
            {
                "model_id": "planner",
                "service_type": "openai",
                "auth_mode": "account",
                "enabled": True,
                "assignments": [{"role": "planner", "priority": 0}],
            },
        ],
    )

    target = write_node_model_configuration(settings)

    assert target == account_root / "node-runtime/node-models.json"
    assert (account_root / "node-runtime/coder/auth.json").is_file()
    assert not (account_root / "node-runtime/planner").exists()
    assert '"model_id":"coder"' in target.read_text(encoding="utf-8")
    assert '"model_id":"planner"' not in target.read_text(encoding="utf-8")
    assert target.stat().st_mode & 0o777 == 0o600


def test_coder_uses_model_card_proxy_before_global_proxy():
    settings = configured_settings(
        openai_proxy_url="http://global-proxy.test:7890",
        model_cards=[
            {
                "model_id": "coder",
                "service_type": "openai",
                "auth_mode": "account",
                "enabled": True,
                "model": "",
                "proxy_url": "http://card-proxy.test:7890",
                "assignments": [{"role": "coder", "priority": 0}],
            }
        ],
    )

    router = build_coder(settings)

    assert router.providers[0].proxy_url == "http://card-proxy.test:7890"


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
