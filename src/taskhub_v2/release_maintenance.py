"""Release-time maintenance commands executed inside the Seed container."""

from __future__ import annotations

import argparse
import asyncio

from taskhub_v2.config import get_settings
from taskhub_v2.domain.configuration import PlatformSettingsUpdate
from taskhub_v2.persistence.configuration import configuration_store
from taskhub_v2.security import build_secret_cipher
from taskhub_v2.services.configuration import ManagedConfigurationService


async def reconcile_node_image(
    configuration: ManagedConfigurationService,
    previous_image: str,
    current_image: str,
) -> str:
    """Advance an inherited managed image while preserving explicit operator overrides."""
    platform = await configuration.platform_settings()
    desired_image = str(platform["desired"].get("node_container_image") or "")
    if desired_image == current_image:
        return "current"
    if desired_image != previous_image:
        return "preserved"
    await configuration.update_platform_settings(
        PlatformSettingsUpdate(node_container_image=current_image),
        operator="deployment-upgrade",
    )
    return "changed"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    reconcile = commands.add_parser(
        "reconcile-node-image",
        help="Advance a managed node image only when it still matches the previous release",
    )
    reconcile.add_argument("--previous", required=True)
    reconcile.add_argument("--current", required=True)
    return parser


async def _run(args: argparse.Namespace) -> str:
    settings = get_settings()
    async with configuration_store(settings) as store:
        configuration = ManagedConfigurationService(
            settings,
            store,
            build_secret_cipher(settings.config_encryption_key),
        )
        return await reconcile_node_image(configuration, args.previous, args.current)


def main() -> None:
    args = _parser().parse_args()
    if args.command == "reconcile-node-image":
        print(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
