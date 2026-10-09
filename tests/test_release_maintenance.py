import asyncio

from cryptography.fernet import Fernet

from taskhub_v2.config import Settings
from taskhub_v2.domain.configuration import PlatformSettingsUpdate
from taskhub_v2.persistence.configuration import MemoryConfigurationStore
from taskhub_v2.release_maintenance import reconcile_node_image
from taskhub_v2.security import SecretCipher
from taskhub_v2.services.configuration import ManagedConfigurationService


def configuration(image: str):
    settings = Settings(
        config_encryption_key=Fernet.generate_key().decode(),
        node_container_image=image,
    )
    return ManagedConfigurationService(
        settings,
        MemoryConfigurationStore(),
        SecretCipher(settings.config_encryption_key),
    )


def test_reconcile_advances_inherited_managed_node_image():
    async def scenario():
        service = configuration("taskhub-node:new")
        await service.update_platform_settings(
            PlatformSettingsUpdate(node_container_image="taskhub-node:old")
        )
        state = await reconcile_node_image(service, "taskhub-node:old", "taskhub-node:new")
        return state, await service.platform_settings()

    state, platform = asyncio.run(scenario())

    assert state == "changed"
    assert platform["desired"]["node_container_image"] == "taskhub-node:new"
    assert platform["restart_required"] is True


def test_reconcile_preserves_explicit_operator_image_override():
    async def scenario():
        service = configuration("taskhub-node:new")
        await service.update_platform_settings(
            PlatformSettingsUpdate(node_container_image="registry.example/custom-node:stable")
        )
        state = await reconcile_node_image(service, "taskhub-node:old", "taskhub-node:new")
        return state, await service.platform_settings()

    state, platform = asyncio.run(scenario())

    assert state == "preserved"
    assert platform["desired"]["node_container_image"] == ("registry.example/custom-node:stable")


def test_reconcile_leaves_current_deployment_default_unchanged():
    async def scenario():
        service = configuration("taskhub-node:new")
        state = await reconcile_node_image(service, "taskhub-node:old", "taskhub-node:new")
        return state, await service.platform_settings()

    state, platform = asyncio.run(scenario())

    assert state == "current"
    assert platform["restart_required"] is False
