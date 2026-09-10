from __future__ import annotations

import asyncio
import hashlib
import os
import re
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from taskhub_v2.services.containers import DockerUnavailableError
from taskhub_v2.services.onboarding import onboarding_status

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])
MAX_OFFLINE_IMAGE_BYTES = 20 * 1024 * 1024 * 1024
IMAGE_PATTERN = re.compile(r"^[A-Za-z0-9._:/@-]+$")


@router.get("/status")
async def status(request: Request) -> dict:
    try:
        return await onboarding_status(request)
    except DockerUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/offline-image", status_code=201)
async def import_offline_image(request: Request) -> dict:
    image = request.headers.get("x-taskhub-image", "").strip()
    if not IMAGE_PATTERN.fullmatch(image):
        raise HTTPException(status_code=422, detail="请提供有效的工作节点镜像名称")
    try:
        content_length = int(request.headers.get("content-length") or 0)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="上传内容长度无效") from exc
    if content_length > MAX_OFFLINE_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="离线镜像包不得超过 20 GB")
    descriptor, temporary_name = tempfile.mkstemp(prefix="taskhub-offline-", suffix=".tar")
    archive = Path(temporary_name)
    size = 0
    digest = hashlib.sha256()
    try:
        with os.fdopen(descriptor, "wb") as target:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_OFFLINE_IMAGE_BYTES:
                    raise HTTPException(status_code=413, detail="离线镜像包不得超过 20 GB")
                target.write(chunk)
                digest.update(chunk)
        if not size:
            raise HTTPException(status_code=422, detail="请选择由 docker save 生成的镜像包")
        try:
            result = await asyncio.to_thread(
                request.app.state.container_manager.import_image, archive, image
            )
        except DockerUnavailableError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {**result, "archive_sha256": digest.hexdigest()}
    finally:
        archive.unlink(missing_ok=True)


@router.post("/restart", status_code=202)
async def restart_seed(request: Request) -> dict:
    if request.app.state.settings.env != "seed":
        raise HTTPException(status_code=409, detail="只有 Seed 容器部署支持 Web 快速重启")

    async def terminate() -> None:
        await asyncio.sleep(0.8)
        os._exit(0)

    asyncio.create_task(terminate())
    return {"accepted": True, "detail": "Seed 正在重启，页面将自动等待服务恢复"}
