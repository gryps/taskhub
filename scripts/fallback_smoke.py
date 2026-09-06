#!/usr/bin/env python3
import asyncio

from taskhub_v2.config import get_settings
from taskhub_v2.providers.factory import build_provider


async def main() -> None:
    settings = get_settings().model_copy(update={"provider": "routed"})
    provider = build_provider(settings)
    try:
        result = await provider.create_plan(
            "Add a health label to a demo page. Produce three concise implementation steps."
        )
        print(f"provider={result.provider}")
        print(f"model={result.model}")
        print(f"fallback={','.join(result.failed_providers) or 'none'}")
        print(f"steps={len(result.content.steps)}")
    finally:
        await provider.close()


if __name__ == "__main__":
    asyncio.run(main())
