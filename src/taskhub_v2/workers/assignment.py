async def execute_with_assignment(
    worker,
    run_id,
    project_id,
    requirement,
    plan,
    *,
    base_commit,
    task_context,
    assignment_callback,
):
    options = {"base_commit": base_commit, "task_context": task_context}
    if getattr(worker, "supports_assignment_callbacks", False):
        options["assignment_callback"] = assignment_callback
    else:
        await assignment_callback("controller-local")
    return await worker.execute(run_id, project_id, requirement, plan, **options)
