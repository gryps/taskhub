import asyncio
import json

import httpx
import pytest

from taskhub_v2.providers.openai import (
    OpenAIRequestError,
    OpenAIResponsesProvider,
    ProviderConfigurationError,
)


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
                                "text": (
                                    '{"summary":"Do it","steps":["code"],'
                                    '"acceptance":["pass"]}'
                                ),
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


def test_openai_supervision_uses_strict_backward_compatible_schema():
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        payload = json.loads(body)
        requests.append(payload)
        schema = payload["text"]["format"]["schema"]
        assert set(schema["required"]) == set(schema["properties"])
        assert "missing_evidence" in schema["required"]
        return httpx.Response(
            200,
            json={
                "output": [{
                    "content": [{
                        "type": "output_text",
                        "text": (
                            '{"decision":"approve","summary":"ready",'
                            '"reasons":[],"missing_evidence":[]}'
                        ),
                    }]
                }]
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
        result = await provider.supervise("requirement", "implementation", "review", "risk")
        await client.aclose()
        assert result.content.decision == "approve"
        assert result.content.missing_evidence == []
        prompt = requests[0]["input"]
        assert "A missing_evidence category means no passed evidence" in prompt
        assert "Do not invent a database-engine" in prompt

    asyncio.run(scenario())


def test_supervision_decision_still_accepts_old_checkpoint_shape():
    from taskhub_v2.domain.models import SupervisionDecision

    decision = SupervisionDecision.model_validate(
        {"decision": "approve", "summary": "ready", "reasons": []}
    )
    assert decision.missing_evidence == []


def test_openai_provider_preserves_sanitized_http_failure():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={"error": {"code": "model_not_found", "message": "Model missing; sk-secret"}},
        )

    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = OpenAIResponsesProvider(
            base_url="https://api.openai.com/v1",
            api_key="secret",
            model="missing-model",
            proxy_url="http://proxy.local:7893",
            client=client,
        )
        with pytest.raises(OpenAIRequestError) as caught:
            await provider.review("requirement", "implementation")
        await client.aclose()
        assert caught.value.status_code == 404
        assert caught.value.reason == "model_not_found"
        assert caught.value.diagnostic == "HTTP 404: Model missing; sk-***"
        assert "sk-secret" not in str(caught.value)

    asyncio.run(scenario())
