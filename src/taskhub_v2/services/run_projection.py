from taskhub_v2.domain.models import RunView


def visible_run_status(checkpoint_status, indexed):
    return indexed.status if indexed and indexed.archived_at else checkpoint_status


async def with_live_execution(view: RunView, production_runtime) -> RunView:
    """Overlay checkpoint fields with the production store's current DAG facts."""
    if production_runtime is None:
        return view
    execution = await production_runtime.view(view.project_id, view.run_id)
    plan = execution.get("execution_plan")
    if plan is None:
        return view

    def dump(record):
        return record.model_dump(mode="json")

    snapshot = execution.get("latest_snapshot")
    return view.model_copy(
        update={
            "execution_plan": dump(plan),
            "production_tasks": [dump(item) for item in execution["tasks"]],
            "execution_batches": [dump(item) for item in execution["batches"]],
            "dag_snapshot": dump(snapshot) if snapshot else None,
        }
    )
