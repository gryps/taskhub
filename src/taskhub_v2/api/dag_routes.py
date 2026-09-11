from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter(prefix="/api", tags=["execution-plans"])


@router.get("/runs/{run_id}/execution-plan")
async def execution_plan_for_run(
    run_id: str,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=200),
):
    runtime = getattr(request.app.state, "dag_runtime", None)
    if runtime is None:
        return {
            "enabled": False,
            "execution_plan": None,
            "tasks": [],
            "batches": [],
            "attempts": [],
        }
    try:
        run = await request.app.state.run_service.get(run_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail="run not found") from error
    result = await runtime.view(run.project_id, run_id)
    tasks = result["tasks"]
    start = (page - 1) * page_size
    return {
        **result,
        "enabled": True,
        "tasks": tasks[start : start + page_size],
        "task_page": {"page": page, "page_size": page_size, "total": len(tasks)},
    }
