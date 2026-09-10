from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from taskhub_v2.services.containers import DockerUnavailableError


def _step(step_id: str, title: str, complete: bool, detail: str, target: str) -> dict:
    return {
        "id": step_id,
        "title": title,
        "complete": complete,
        "status": "complete" if complete else "pending",
        "detail": detail,
        "target": target,
    }


def _nearest_existing(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def seed_storage(settings) -> dict[str, Any]:
    target = _nearest_existing(Path(settings.artifact_root))
    usage = shutil.disk_usage(target)
    return {
        "path": str(target),
        "total_bytes": usage.total,
        "free_bytes": usage.free,
        "used_percent": round((usage.used / usage.total) * 100, 1) if usage.total else 0,
    }


def model_ready(settings) -> tuple[bool, str]:
    if settings.provider == "deterministic":
        return True, "内置确定性模型已启用，可用于流程验证"
    if settings.provider == "openai":
        ready = bool(settings.openai_api_key and settings.openai_model)
        return ready, "OpenAI 模型配置已生效" if ready else "OpenAI API Key 或模型尚未生效"
    required = (
        settings.gpt_api_key,
        settings.gpt_model,
        settings.deepseek_api_key,
        settings.deepseek_model,
        settings.minimax_api_key,
        settings.minimax_model,
    )
    ready = all(required)
    return ready, "多模型路由配置已生效" if ready else "多模型路由所需凭据尚未全部生效"


async def onboarding_status(request) -> dict[str, Any]:
    settings = request.app.state.settings
    manager = request.app.state.container_manager
    try:
        docker = manager.status()
    except DockerUnavailableError as exc:
        docker = {"enabled": True, "available": False, "detail": str(exc), "engine": {}}
    storage = seed_storage(settings)
    engine = docker.get("engine") or {}
    hardware_ready = bool(
        docker.get("available")
        and engine.get("cpu_count")
        and engine.get("memory_total_bytes")
        and storage.get("free_bytes")
    )

    platform = await request.app.state.managed_configuration.platform_settings()
    platform_desired = platform["desired"]
    addresses_present = bool(
        platform_desired.get("seed_public_url") and platform_desired.get("node_callback_url")
    )
    addresses_ready = addresses_present and not platform["restart_required"]

    try:
        image = manager.image_metadata(platform_desired["node_container_image"])
        image_local = True
    except DockerUnavailableError:
        image = {}
        image_local = False
    remote_source = bool(
        platform_desired.get("node_image_registry")
        or platform_desired.get("node_image_proxy")
    )
    image_ready = image_local or remote_source

    models_ready, models_detail = model_ready(settings)
    model_configuration = await request.app.state.managed_configuration.model_services()
    if model_configuration["restart_required"]:
        models_ready = False
        models_detail = "模型配置已保存，等待重启 Seed 后生效"

    hosts = (await request.app.state.physical_hosts.list())["hosts"]
    available_hosts = [item for item in hosts if item["status"] == "available"]
    nodes = await request.app.state.node_scheduler.status()
    online_nodes = [item for item in nodes if item["status"] == "ok"]
    administrator_ready = not request.app.state.auth.setup_required()

    steps = [
        _step(
            "administrator",
            "管理员密码",
            administrator_ready,
            "管理员密码已设置" if administrator_ready else "请先设置管理员密码",
            "authentication",
        ),
        _step(
            "seed_host",
            "Seed 主机检测",
            hardware_ready,
            (
                f"Docker {engine.get('version') or '可用'} · "
                f"{engine.get('cpu_count') or '—'} CPU · "
                f"{engine.get('memory_total_bytes') or 0} 字节内存 · "
                f"{storage['free_bytes']} 字节可用磁盘"
                if hardware_ready
                else docker.get("detail", "无法读取 Docker、CPU、内存或磁盘信息")
            ),
            "system",
        ),
        _step(
            "addresses",
            "Seed 与节点地址",
            addresses_ready,
            (
                "对外地址和节点回连地址已生效"
                if addresses_ready
                else "地址已保存，等待重启生效"
                if addresses_present
                else "请设置 Seed 对外地址和节点回连地址"
            ),
            "platform",
        ),
        _step(
            "image_source",
            "工作节点镜像",
            image_ready,
            (
                f"Seed 已有镜像 {platform_desired['node_container_image']}"
                if image_local
                else "已配置远程镜像仓库或代理"
                if remote_source
                else "请配置镜像仓库，或导入离线镜像包"
            ),
            "image",
        ),
        _step("models", "模型服务", models_ready, models_detail, "providers"),
        _step(
            "physical_host",
            "第一台物理主机",
            bool(available_hosts),
            (
                f"{len(available_hosts)} 台主机已通过准入"
                if available_hosts
                else "尚无通过准入的远程主机"
            ),
            "hosts",
        ),
        _step(
            "work_node",
            "第一个工作节点",
            bool(online_nodes),
            (
                f"{len(online_nodes)} 个节点在线并可调度"
                if online_nodes
                else "尚无健康且可调度的工作节点"
            ),
            "nodes",
        ),
    ]
    ready = all(item["complete"] for item in steps)
    return {
        "ready": ready,
        "message": "系统已具备运行任务条件" if ready else "系统尚未具备运行任务条件",
        "completed": sum(item["complete"] for item in steps),
        "total": len(steps),
        "restart_required": bool(
            platform["restart_required"] or model_configuration["restart_required"]
        ),
        "steps": steps,
        "seed": {"docker": docker, "storage": storage},
        "image": {
            "reference": platform_desired["node_container_image"],
            "local": image_local,
            "metadata": image,
            "remote_source_configured": remote_source,
        },
    }
