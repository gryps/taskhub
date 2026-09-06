import asyncio

import httpx
import pytest

from taskhub_v2.providers.openai import OpenAIResponsesProvider, ProviderConfigurationError


def test_openai_provider_requires_proxy():
    with pytest.raises(ProviderConfigurationError, match="proxy"):
        OpenAIResponsesProvider(
            base_url="https://api.openai.com/v1",
            api_key="secret",
            model="test-model",
            proxy_url="",
        )


def test_openai_provider_parses_structured_plan():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/responses"
        assert request.headers["Authorization"] == "Bearer secret"
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"summary":"Do it","steps":["code"],"acceptance":["pass"]}',
                            }
                        ]
                    }
                ]
            },
        )

    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = OpenAIResponsesProvider(
            base_url="https://api.openai.com/v1",
            api_key="secret",
            model="test-model",
            proxy_url="http://proxy.local:7893",
            client=client,
        )
        plan = await provider.create_plan("Build search")
        await client.aclose()
        assert plan.content.summary == "Do it"
        assert plan.content.steps == ["code"]

    asyncio.run(scenario())
