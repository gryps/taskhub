#!/usr/bin/env python3
import asyncio

from taskhub_v2.config import get_settings
from taskhub_v2.providers.factory import build_provider_registry


async def main() -> None:
    providers = build_provider_registry(get_settings())
    try:
        for provider_id, provider in providers.items():
            try:
                result = await provider.review(
                    "Provider connectivity check; reply with OK only.",
                    "No repository or code operation is requested.",
                )
                print(
                    f"{provider_id}: ok model={result.model} duration_ms={result.duration_ms}"
                )
            except Exception as exc:
                print(f"{provider_id}: failed error={exc.__class__.__name__} detail={exc}")
    finally:
        for provider in providers.values():
            close = getattr(provider, "close", None)
            if close:
                await close()


if __name__ == "__main__":
    asyncio.run(main())
