import json
import time
from typing import Any

import httpx

from taskhub_v2.domain.models import ModelResult, Plan, SupervisionDecision
from taskhub_v2.providers.prompts import EVIDENCE_OWNERSHIP_POLICY


class ChatCompatibleProvider:
    def __init__(
        self,
        provider_id: str,
        base_url: str,
        api_key: str,
        model: str,
        client: httpx.AsyncClient | None = None,
    ):
        if not api_key or not model:
            raise ValueError(f"{provider_id} API key and model are required")
        self.provider_id = provider_id
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.client = client or httpx.AsyncClient(trust_env=False, timeout=120)
        self.owns_client = client is None

    async def close(self) -> None:
        if self.owns_client:
            await self.client.aclose()

    async def create_plan(self, requirement: str) -> ModelResult[Plan]:
        prompt = (
            "Return JSON only with keys summary, steps, acceptance. "
            "steps and acceptance must be arrays of strings.\nRequirement:\n" + requirement
        )
        text, duration, usage = await self._complete(prompt)
        return ModelResult(
            content=Plan.model_validate(self._json_object(text)),
            provider=self.provider_id,
            model=self.model,
            duration_ms=duration,
            usage=usage,
        )

    async def review(self, requirement: str, implementation: str) -> ModelResult[str]:
        prompt = (
            f"Review this implementation against the requirement. Be concise. "
            f"{EVIDENCE_OWNERSHIP_POLICY}\n"
            f"Requirement: {requirement}\nEvidence: {implementation}"
        )
        return await self._text_result(prompt)

    async def assess_risk(self, requirement: str, implementation: str) -> ModelResult[str]:
        prompt = (
            f"Assess delivery risks and missing evidence. Be concise. "
            f"{EVIDENCE_OWNERSHIP_POLICY}\n"
            f"Requirement: {requirement}\nEvidence: {implementation}"
        )
        return await self._text_result(prompt)

    async def supervise(
        self, requirement: str, implementation: str, review: str, risk: str
    ) -> ModelResult[SupervisionDecision]:
        prompt = (
            "Return JSON only with decision (approve or reject), summary, and reasons array.\n"
            f"{EVIDENCE_OWNERSHIP_POLICY}\n"
            f"Requirement: {requirement}\nImplementation: {implementation}\n"
            f"Review: {review}\nRisk: {risk}"
        )
        text, duration, usage = await self._complete(prompt)
        return ModelResult(
            content=SupervisionDecision.model_validate(self._json_object(text)),
            provider=self.provider_id,
            model=self.model,
            duration_ms=duration,
            usage=usage,
        )

    async def _text_result(self, prompt: str) -> ModelResult[str]:
        text, duration, usage = await self._complete(prompt)
        return ModelResult(
            content=text,
            provider=self.provider_id,
            model=self.model,
            duration_ms=duration,
            usage=usage,
        )

    async def _complete(self, prompt: str) -> tuple[str, int, dict[str, Any]]:
        started = time.monotonic()
        response = await self.client.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "messages": [{"role": "user", "content": prompt}]},
        )
        response.raise_for_status()
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        return content, int((time.monotonic() - started) * 1000), payload.get("usage") or {}

    @staticmethod
    def _json_object(text: str) -> dict[str, Any]:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0]
        return json.loads(cleaned)
