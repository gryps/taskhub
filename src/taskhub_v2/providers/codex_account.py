import asyncio
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from taskhub_v2.domain.models import (
    CodeChangeSummary,
    ModelResult,
    Plan,
    SupervisionDecision,
)
from taskhub_v2.providers.egress import (
    PROXY_VARIABLES,
    account_environment,
    direct_environment,
    provider_proxy,
)

_ACCOUNT_LOCKS: dict[str, asyncio.Lock] = {}


class CodexAccountError(RuntimeError):
    def __init__(self, reason: str, diagnostic: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.diagnostic = diagnostic


class CodexAccountProvider:
    def __init__(
        self,
        provider_id: str,
        codex_bin: str,
        codex_home: str,
        proxy_url: str,
        workdir: str,
        model: str = "account_default",
        timeout: int = 600,
        api_key: str = "",
    ):
        self.provider_id = provider_id
        self.codex_bin = codex_bin
        self.codex_home = codex_home
        self.proxy_url = proxy_url
        self.workdir = workdir
        self.model = model
        self.timeout = timeout
        self.api_key = api_key
        self.lock = _ACCOUNT_LOCKS.setdefault(str(Path(codex_home).resolve()), asyncio.Lock())

    async def status(self) -> dict[str, Any]:
        auth_file = Path(self.codex_home) / "auth.json"
        if not Path(self.codex_bin).is_file():
            return {"status": "cli_missing", "authenticated": False}
        if not auth_file.is_file():
            return {"status": "needs_reauth", "authenticated": False}
        process = await asyncio.create_subprocess_exec(
            self.codex_bin,
            "login",
            "status",
            env=account_environment(dict(os.environ), self.proxy_url, self.codex_home),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, _ = await asyncio.wait_for(process.communicate(), timeout=15)
        except TimeoutError:
            process.kill()
            await process.wait()
            return {"status": "timeout", "authenticated": False}
        return {
            "status": "available" if process.returncode == 0 else "needs_reauth",
            "authenticated": process.returncode == 0,
        }

    async def create_plan(self, requirement: str) -> ModelResult[Plan]:
        prompt = "Create a concise implementation plan for this requirement:\n" + requirement
        text, duration = await self._run(prompt, Plan.model_json_schema())
        return self._result(Plan.model_validate_json(text), duration)

    async def review(self, requirement: str, implementation: str) -> ModelResult[str]:
        prompt = f"Review implementation evidence.\nRequirement: {requirement}\n{implementation}"
        text, duration = await self._run(prompt)
        return self._result(text, duration)

    async def assess_risk(self, requirement: str, implementation: str) -> ModelResult[str]:
        prompt = f"Assess delivery risk.\nRequirement: {requirement}\n{implementation}"
        text, duration = await self._run(prompt)
        return self._result(text, duration)

    async def supervise(
        self, requirement: str, implementation: str, review: str, risk: str
    ) -> ModelResult[SupervisionDecision]:
        prompt = (
            "Make the final delivery decision from the evidence. Reject if evidence is "
            "insufficient or a material defect remains. Classify every evidence-only gap in "
            "missing_evidence so the workflow can request evidence without sending it to the "
            "coding worker. Use browser, database, openapi, test, or manual as appropriate. "
            "Leave missing_evidence empty only when source changes are required. Do not require "
            "a production deployment or production-data rehearsal unless the requirement or "
            "acceptance criteria explicitly require it.\n"
            f"Requirement:\n{requirement}\nImplementation:\n{implementation}\n"
            f"Review:\n{review}\nRisk:\n{risk}"
        )
        text, duration = await self._run(prompt, SupervisionDecision.response_json_schema())
        return self._result(SupervisionDecision.model_validate_json(text), duration)

    async def modify_workspace(
        self, requirement: str, plan: Plan, workdir: str, feedback: str = ""
    ) -> ModelResult[CodeChangeSummary]:
        prompt = (
            "Implement the approved requirement in the current Git worktree. "
            "You are the authorized TaskHub coding worker for managed-project changes; "
            "make every required source and test change yourself in this worktree so "
            "the work is attributable to this run. "
            "Do not commit. Do not modify credentials or environment files. "
            "Return a concise summary and tests you ran.\n"
            f"Requirement:\n{requirement}\nApproved plan:\n{plan.model_dump_json()}"
        )
        if feedback:
            prompt += (
                "\nThis is a revision of the existing implementation. Resolve every "
                "supervisor finding below and preserve correct existing changes.\n"
                f"Supervisor findings:\n{feedback}"
            )
        text, duration = await self._run(
            prompt,
            CodeChangeSummary.model_json_schema(),
            workdir=workdir,
            sandbox="workspace-write",
        )
        return self._result(CodeChangeSummary.model_validate_json(text), duration)

    def _result(self, content: Any, duration: int) -> ModelResult:
        return ModelResult(
            content=content,
            provider=self.provider_id,
            model=self.model,
            duration_ms=duration,
        )

    async def _run(
        self,
        prompt: str,
        schema: dict[str, Any] | None = None,
        workdir: str | None = None,
        sandbox: str = "read-only",
    ) -> tuple[str, int]:
        async with self.lock:
            with tempfile.TemporaryDirectory(prefix=f"taskhub-{self.provider_id}-") as temp:
                output = Path(temp) / "last-message.txt"
                command = self._command(
                    output, Path(temp), schema, workdir or self.workdir, sandbox
                )
                started = time.monotonic()
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=self._environment(),
                )
                try:
                    stdout, stderr = await asyncio.wait_for(
                        process.communicate(prompt.encode()), timeout=self.timeout
                    )
                except TimeoutError as exc:
                    process.kill()
                    await process.wait()
                    raise CodexAccountError("Codex account runner timed out") from exc
                if process.returncode or not output.exists():
                    raw_error = (stdout + stderr).decode(errors="replace")
                    reason = self._failure_reason(raw_error)
                    raise CodexAccountError(reason, self._safe_diagnostic(raw_error))
                duration = int((time.monotonic() - started) * 1000)
                return output.read_text(encoding="utf-8"), duration

    def _command(
        self,
        output: Path,
        temp: Path,
        schema: dict[str, Any] | None,
        workdir: str,
        sandbox: str,
    ) -> list[str]:
        command = [
            self.codex_bin,
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--sandbox",
            sandbox,
            "--skip-git-repo-check",
            "--color",
            "never",
            "--cd",
            workdir,
            "--output-last-message",
            str(output),
        ]
        if self.model != "account_default":
            command.extend(["--model", self.model])
        if schema:
            schema_path = temp / "schema.json"
            schema_path.write_text(json.dumps(schema), encoding="utf-8")
            command.extend(["--output-schema", str(schema_path)])
        return [*command, "-"]

    def _environment(self) -> dict[str, str]:
        home = Path(self.codex_home)
        home.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(home, 0o700)
        if not self.api_key:
            return account_environment(dict(os.environ), self.proxy_url, self.codex_home)
        result = direct_environment(dict(os.environ))
        proxy = provider_proxy("gpt_api", self.proxy_url) or ""
        for key in PROXY_VARIABLES:
            result[key] = proxy
        result["CODEX_HOME"] = self.codex_home
        result["OPENAI_API_KEY"] = self.api_key
        return result

    @staticmethod
    def _failure_reason(output: str) -> str:
        lowered = output.lower()
        if any(
            marker in lowered
            for marker in (
                "invalid schema for response_format",
                "invalid json schema",
                "invalid_response_schema",
            )
        ):
            return "invalid_response_schema"
        if re.search(
            r"\b(?:insufficient_quota|usage_limit_reached|quota exceeded)\b",
            lowered,
        ) or re.search(
            r"\byou(?:'ve| have) (?:hit|reached) (?:your )?(?:current )?usage limit\b",
            lowered,
        ):
            return "quota_exceeded"
        if re.search(
            r"\b(?:http(?: status)?|unexpected status)[: =]+401\b",
            lowered,
        ) or any(
            marker in lowered
            for marker in ('"code":"invalid_api_key"', '"code": "invalid_api_key"')
        ):
            return "needs_reauth"
        if re.search(
            r"\b(?:http(?: status)?|unexpected status)[: =]+429\b",
            lowered,
        ) or "too many requests" in lowered or re.search(
            r'"code"\s*:\s*"rate_limit_exceeded"', lowered
        ):
            return "rate_limited"
        return "account_runner_failed"

    @staticmethod
    def _safe_diagnostic(output: str) -> str:
        tail = output[-1600:]
        tail = re.sub(r"sk-[A-Za-z0-9_-]+", "sk-***", tail)
        tail = re.sub(r"(?i)(authorization[:=]\s*bearer\s+)\S+", r"\1***", tail)
        return tail
