from taskhub_v2.domain.models import CodeChangeSummary, ModelResult, Plan
from taskhub_v2.providers.codex_account import CodexAccountProvider
from taskhub_v2.providers.health import ProviderHealthStore


class CodingProvidersExhausted(RuntimeError):
    def __init__(self, failures: list[str]):
        super().__init__(f"coding providers exhausted: {', '.join(failures)}")
        self.failures = failures


class CodexCodingRouter:
    def __init__(
        self,
        providers: list[CodexAccountProvider],
        health: ProviderHealthStore | None = None,
    ):
        self.providers = providers
        self.health = health

    async def modify(
        self,
        requirement: str,
        plan: Plan,
        workdir: str,
        feedback: str = "",
        task_context: dict | None = None,
    ) -> ModelResult[CodeChangeSummary]:
        failures = []
        for provider in self.providers:
            available, health = (
                self.health.availability(provider.provider_id) if self.health else (True, None)
            )
            if not available:
                failures.append(f"{provider.provider_id}:cooldown:{health['reason']}")
                continue
            try:
                result = await provider.modify_workspace(
                    requirement, plan, workdir, feedback=feedback
                )
                if self.health and not self.health.record_success(provider.provider_id):
                    failures.append(f"{provider.provider_id}:recovery_probe")
                    continue
                result.failed_providers = failures
                return result
            except Exception as exc:
                reason = getattr(exc, "reason", exc.__class__.__name__)
                if self.health:
                    self.health.record_failure(provider.provider_id, reason)
                failures.append(f"{provider.provider_id}:{reason}")
        raise CodingProvidersExhausted(failures)


class ScheduledCodingRouter:
    def __init__(self, scheduler, topology_resolver=None):
        self.scheduler = scheduler
        self.topology_resolver = topology_resolver

    async def modify(
        self,
        requirement: str,
        plan: Plan,
        workdir: str,
        feedback: str = "",
        task_context: dict | None = None,
    ) -> ModelResult[CodeChangeSummary]:
        from pathlib import Path

        context = task_context or {}
        task_id = str(context.get("task_id") or Path(workdir).name)
        job_id = f"{task_id}-code"
        eligible = (
            await self.topology_resolver(str(context.get("project_id") or ""), "coding")
            if self.topology_resolver and context.get("project_id")
            else None
        )
        scheduled = await self.scheduler.run_coding(
            job_id,
            task_id,
            requirement,
            plan,
            feedback,
            1200,
            workdir,
            required_capabilities_override=set(context.get("required_capabilities") or []),
            eligible_node_ids=eligible,
        )
        scheduled.result.usage["coding_node"] = scheduled.node_id
        return scheduled.result
