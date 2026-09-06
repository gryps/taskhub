import json
import time
from typing import Any

import httpx

from taskhub_v2.domain.models import ModelResult, Plan, SupervisionDecision


class ProviderConfigurationError(ValueError):
    pass


class OpenAIResponsesProvider:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        proxy_url: str,
        planner_model: str | None = None,
        supervisor_model: str | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        if not api_key or not model:
            raise ProviderConfigurationError("OpenAI API key and model are required")
        if not proxy_url:
            raise ProviderConfigurationError("OpenAI traffic requires a proxy URL")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.planner_model = planner_model or model
        self.supervisor_model = supervisor_model or model
        self.client = client or httpx.AsyncClient(
            proxy=proxy_url, trust_env=False, timeout=120
        )
        self.owns_client = client is None

    async def close(self) -> None:
        if self.owns_client:
            await self.client.aclose()

    async def create_plan(self, requirement: str) -> ModelResult[Plan]:
        started = time.monotonic()
        payload = await self._request(
            [
                {
                    "role": "developer",
                    "content": "Create a concise implementation plan with verifiable acceptance criteria.",
                },
                {"role": "user", "content": requirement},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "implementation_plan",
                    "strict": True,
                    "schema": Plan.model_json_schema(),
                }
            },
            model=self.planner_model,
        )
        return ModelResult(
            content=Plan.model_validate_json(self._output_text(payload)),
            provider="gpt_api",
            model=self.planner_model,
            duration_ms=int((time.monotonic() - started) * 1000),
            usage=payload.get("usage") or {},
        )

    async def review(self, requirement: str, implementation: str) -> ModelResult[str]:
        started = time.monotonic()
        payload = await self._request(
            f"Review this implementation against the requirement.\nRequirement: {requirement}\n"
            f"Evidence: {implementation}"
        )
        return ModelResult(
            content=self._output_text(payload),
            provider="gpt_api",
            model=self.model,
            duration_ms=int((time.monotonic() - started) * 1000),
            usage=payload.get("usage") or {},
        )

    async def assess_risk(self, requirement: str, implementation: str) -> ModelResult[str]:
        started = time.monotonic()
        payload = await self._request(
            f"Assess delivery risks briefly.\nRequirement: {requirement}\nEvidence: {implementation}"
        )
        return ModelResult(
            content=self._output_text(payload),
            provider="gpt_api",
            model=self.model,
            duration_ms=int((time.monotonic() - started) * 1000),
            usage=payload.get("usage") or {},
        )

    async def supervise(
        self, requirement: str, implementation: str, review: str, risk: str
    ) -> ModelResult[SupervisionDecision]:
        started = time.monotonic()
        payload = await self._request(
            (
                "Make the final delivery decision. Reject if evidence is insufficient or a "
                "material defect remains.\n"
                f"Requirement: {requirement}\nImplementation: {implementation}\n"
                f"Review: {review}\nRisk: {risk}"
            ),
            text={
                "format": {
                    "type": "json_schema",
                    "name": "supervision_decision",
                    "strict": True,
                    "schema": SupervisionDecision.model_json_schema(),
                }
            },
            model=self.supervisor_model,
        )
        return ModelResult(
            content=SupervisionDecision.model_validate_json(self._output_text(payload)),
            provider="gpt_api",
            model=self.supervisor_model,
            duration_ms=int((time.monotonic() - started) * 1000),
            usage=payload.get("usage") or {},
        )

    async def _request(
        self, input_value: Any, model: str | None = None, **extra: Any
    ) -> dict[str, Any]:
        response = await self.client.post(
            f"{self.base_url}/responses",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": model or self.model, "input": input_value, **extra},
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _output_text(payload: dict[str, Any]) -> str:
        for output in payload.get("output", []):
            for content in output.get("content", []):
                if content.get("type") == "output_text" and content.get("text"):
                    return content["text"]
        if isinstance(payload.get("output_text"), str):
            return payload["output_text"]
        raise ValueError(f"Responses API returned no output text: {json.dumps(payload)[:300]}")
