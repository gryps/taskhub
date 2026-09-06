from fastapi import Request

from taskhub_v2.services.runs import RunService


def get_run_service(request: Request) -> RunService:
    return request.app.state.run_service
