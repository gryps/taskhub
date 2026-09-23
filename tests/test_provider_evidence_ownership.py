import asyncio
import json

import httpx

from taskhub_v2.providers.chat_compatible import ChatCompatibleProvider
from taskhub_v2.providers.codex_account import CodexAccountProvider


def test_codex_review_and_risk_prompts_preserve_evidence_ownership(monkeypatch):
    prompts = []

    async def fake_run(self, prompt, schema=None, **kwargs):
        prompts.append(prompt)
        return "ok", 1

    monkeypatch.setattr(CodexAccountProvider, "_run", fake_run)
    provider = CodexAccountProvider(
        provider_id="codex",
        codex_bin="codex",
        codex_home="/tmp/codex",
        proxy_url="http://proxy.example:7890",
        workdir="/tmp",
    )

    async def scenario():
        await provider.review("requirement", "evidence")
        await provider.assess_risk("requirement", "evidence")

    asyncio.run(scenario())
    assert len(prompts) == 2
    assert all("platform-configured verification" in prompt for prompt in prompts)
    assert all("not running bootstrap" in prompt for prompt in prompts)


def test_chat_supervisor_prompt_preserves_evidence_ownership():
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.read()))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"decision": "approve", "summary": "ready", "reasons": []}
                            )
                        }
                    }
                ]
            },
        )

    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = ChatCompatibleProvider(
            provider_id="compatible",
            base_url="https://models.example/v1",
            api_key="secret",
            model="model",
            client=client,
        )
        result = await provider.supervise("requirement", "implementation", "review", "risk")
        await client.aclose()
        assert result.content.decision == "approve"

    asyncio.run(scenario())
    prompt = requests[0]["messages"][0]["content"]
    assert "platform-configured verification" in prompt
    assert "not running bootstrap" in prompt
