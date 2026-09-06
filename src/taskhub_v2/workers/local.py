from taskhub_v2.domain.models import ExecutionResult, Plan


class LocalWorker:
    """Phase-one worker proving orchestration; real Git execution belongs to phase two."""

    async def execute(
        self,
        run_id: str,
        project_id: str,
        requirement: str,
        plan: Plan,
        revision: int = 0,
        feedback: str = "",
    ) -> ExecutionResult:
        return ExecutionResult(
            summary=(
                f"worker=local run={run_id} project={project_id} "
                f"steps={len(plan.steps)} revision={revision}"
            )
        )
