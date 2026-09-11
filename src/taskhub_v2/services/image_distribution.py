from __future__ import annotations

import re
import shlex

from taskhub_v2.domain.remote_nodes import RemoteNodeCreate


class ImageDistributionError(RuntimeError):
    pass


def docker_prefix(access: str) -> str:
    return "sudo -n docker" if access == "sudo" else "docker"


def apply_host_docker_access(script: str, docker_access: str) -> str:
    return script.replace("__TASKHUB_DOCKER__", docker_prefix(docker_access))


def image_sources(image: str, registry: str, proxy: str) -> list[tuple[str, str]]:
    if "@sha256:" in image:
        return [("configured-registry", image)]
    sources = []
    for source, prefix in (("private-registry", registry), ("proxy", proxy)):
        if prefix:
            candidate = image if image.startswith(f"{prefix}/") else f"{prefix}/{image}"
            sources.append((source, candidate))
    sources.append(("configured-registry", image))
    unique = []
    seen = set()
    for item in sources:
        if item[1] not in seen:
            seen.add(item[1])
            unique.append(item)
    return unique


def pull_script(
    image: str, registry: str, proxy: str, username: str, password: str
) -> str:
    quoted_image = shlex.quote(image)
    candidates = image_sources(image, registry, proxy)
    login = ""
    if registry and username and password:
        login_host = registry.split("/", 1)[0]
        login = (
            f"if ! printf '%s' {shlex.quote(password)} | docker_run login "
            f"{shlex.quote(login_host)} --username {shlex.quote(username)} "
            "--password-stdin >/dev/null 2>\"$error_file\"; then\n"
            "  last_error=$(tr '\\n' ' ' < \"$error_file\" | cut -c1-300)\n"
            "fi\n"
        )
    attempts = []
    for source, candidate in candidates:
        tag = ""
        if candidate != image:
            tag = f"docker_run tag {shlex.quote(candidate)} {quoted_image}\n"
        attempts.append(
            f"if docker_run pull {shlex.quote(candidate)} >\"$output_file\" "
            "2>\"$error_file\"; then\n"
            f"  {tag}  emit_image {shlex.quote(source)}\n  exit 0\n"
            "fi\n"
            "last_error=$(tr '\\n' ' ' < \"$error_file\" | cut -c1-300)\n"
        )
    return f"""set -u
docker_run() {{ __TASKHUB_DOCKER__ "$@"; }}
target={quoted_image}
config_dir=$(mktemp -d)
error_file=$(mktemp)
output_file=$(mktemp)
trap 'rm -rf "$config_dir" "$error_file" "$output_file"' EXIT
export DOCKER_CONFIG="$config_dir"
emit_image() {{
  source_value="$1"
  image_id=$(docker_run image inspect "$target" --format '{{{{.Id}}}}')
  image_arch=$(docker_run image inspect "$target" --format '{{{{.Architecture}}}}')
  image_os=$(docker_run image inspect "$target" --format '{{{{.Os}}}}')
  image_digests=$(docker_run image inspect "$target" --format '{{{{join .RepoDigests ","}}}}')
  printf 'FOUND=1\nSOURCE=%s\nIMAGE_ID=%s\nARCH=%s\nOS=%s\nDIGESTS=%s\n' \
    "$source_value" "$image_id" "$image_arch" "$image_os" "$image_digests"
}}
last_error='未找到可用镜像'
{login}{''.join(attempts)}if docker_run image inspect "$target" >/dev/null 2>&1; then
  emit_image existing-cache
  exit 0
fi
printf 'FOUND=0\nERROR=%s\n' "$last_error"
"""


def inspect_script(image: str) -> str:
    return f"""set -eu
docker_run() {{ __TASKHUB_DOCKER__ "$@"; }}
target={shlex.quote(image)}
image_id=$(docker_run image inspect "$target" --format '{{{{.Id}}}}')
image_arch=$(docker_run image inspect "$target" --format '{{{{.Architecture}}}}')
image_os=$(docker_run image inspect "$target" --format '{{{{.Os}}}}')
image_digests=$(docker_run image inspect "$target" --format '{{{{join .RepoDigests ","}}}}')
printf 'FOUND=1\nSOURCE=ssh-transfer\nIMAGE_ID=%s\nARCH=%s\nOS=%s\nDIGESTS=%s\n' \
  "$image_id" "$image_arch" "$image_os" "$image_digests"
"""


def create_script(
    request: RemoteNodeCreate,
    image: str,
    token: str,
    cpu: str,
    memory: str,
    *,
    replace_existing: bool = False,
) -> str:
    name = f"taskhub-node-{request.node_id}"
    quoted_image = shlex.quote(image)
    args = [
        "create",
        "--name",
        name,
        "--label",
        "io.taskhub.managed=true",
        "--label",
        f"io.taskhub.node-id={request.node_id}",
        "--label",
        f"io.taskhub.role={request.role}",
        "--label",
        f"io.taskhub.host-id={request.host_id}",
        "--restart",
        "unless-stopped",
        "--env-file",
        '"$env_file"',
        "-p",
        f"{request.host_port}:8020",
        "-v",
        f"{name}-data:/var/lib/taskhub-node",
    ]
    if cpu:
        args.extend(["--cpus", cpu])
    if memory:
        args.extend(["--memory", memory])
    args.append(image)
    command = " ".join(item if item == '"$env_file"' else shlex.quote(item) for item in args)
    token_line = shlex.quote(f"TASKHUB_NODE_TOKEN={token}")
    existing = (
        f"docker_run rm -f {shlex.quote(name)} >/dev/null\n"
        if replace_existing
        else "echo '节点容器已存在' >&2\n  exit 17\n"
    )
    return f"""set -eu
docker_run() {{ __TASKHUB_DOCKER__ "$@"; }}
if docker_run container inspect {shlex.quote(name)} >/dev/null 2>&1; then
  {existing.rstrip()}
fi
docker_run image inspect {quoted_image} >/dev/null 2>&1
env_file=$(mktemp)
trap 'rm -f "$env_file"' EXIT
chmod 600 "$env_file"
printf '%s\n' 'TASKHUB_NODE_ID={request.node_id}' 'TASKHUB_NODE_ROLE={request.role}' \
  'TASKHUB_NODE_SLOTS={request.slots}' \
  'TASKHUB_NODE_WORK_ROOT=/var/lib/taskhub-node/jobs' {token_line} > "$env_file"
container=$(docker_run {command})
docker_run start "$container" >/dev/null
state=$(docker_run inspect "$container" --format '{{{{.State.Status}}}}')
printf 'CONTAINER=%s\nSTATE=%s\n' "$container" "$state"
"""


def runtime_script(node_id: str, docker_access: str) -> str:
    docker = docker_prefix(docker_access)
    name = shlex.quote(f"taskhub-node-{node_id}")
    return f"""set -eu
if ! {docker} container inspect {name} >/dev/null 2>&1; then
  printf 'EXISTS=0\nSTATE=missing\nCONTAINER=\nIMAGE=\n'
  exit 0
fi
container=$({docker} container inspect {name} --format '{{{{.Id}}}}')
state=$({docker} container inspect {name} --format '{{{{.State.Status}}}}')
image=$({docker} container inspect {name} --format '{{{{.Config.Image}}}}')
printf 'EXISTS=1\nSTATE=%s\nCONTAINER=%s\nIMAGE=%s\n' \
  "$state" "$container" "$image"
"""


def diagnostics_script(node_id: str, docker_access: str, tail: int = 200) -> str:
    docker = docker_prefix(docker_access)
    name = shlex.quote(f"taskhub-node-{node_id}")
    return f"""set -eu
docker_run() {{ {docker} "$@"; }}
stats=$(docker_run stats --no-stream \
  --format '{{{{.CPUPerc}}}}|{{{{.MemUsage}}}}' {name} 2>/dev/null || true)
printf 'TASKHUB_CONTAINER_STATS=%s\n' "$stats"
printf '%s\n' 'TASKHUB_CONTAINER_LOGS_BEGIN'
docker_run logs --timestamps --tail {max(1, min(tail, 500))} {name} 2>&1 || true
"""


def action_script(node_id: str, docker_access: str, action: str, remove_volume: bool) -> str:
    docker = docker_prefix(docker_access)
    name = f"taskhub-node-{node_id}"
    if action == "remove":
        volume = f"{name}-data"
        remove = (
            f"\n{docker} volume rm {shlex.quote(volume)} >/dev/null 2>&1 || true"
            if remove_volume
            else ""
        )
        return (
            f"set -eu\n{docker} rm -f {shlex.quote(name)} >/dev/null 2>&1 || true"
            f"{remove}\n"
        )
    command = {"start": "start", "stop": "stop", "restart": "restart"}[action]
    return (
        f"set -eu\n{docker} {command} {shlex.quote(name)} >/dev/null\n"
        f"state=$({docker} inspect {shlex.quote(name)} --format '{{{{.State.Status}}}}')\n"
        "printf 'STATE=%s\\n' \"$state\"\n"
    )


def validate_remote_image(image: str, host_facts: dict, values: dict[str, str]) -> str:
    if values.get("FOUND") != "1" or not values.get("IMAGE_ID", "").startswith("sha256:"):
        raise ImageDistributionError("远程镜像检查没有返回有效摘要")
    validate_architecture(host_facts, values.get("ARCH", ""), values.get("OS", ""))
    digests = [item for item in values.get("DIGESTS", "").split(",") if item]
    pinned = image.partition("@sha256:")[2]
    if pinned and not any(item.endswith(f"@sha256:{pinned}") for item in digests):
        raise ImageDistributionError("远程镜像摘要与指定的固定摘要不一致")
    return digests[0] if digests else values["IMAGE_ID"]


def validate_architecture(host_facts: dict, image_arch: str, image_os: str) -> None:
    aliases = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
        "armv7l": "arm",
        "arm": "arm",
    }
    host_arch = aliases.get(str(host_facts.get("architecture", "")).lower())
    actual_arch = aliases.get(image_arch.lower())
    if not host_arch or not actual_arch or host_arch != actual_arch:
        raise ImageDistributionError(
            f"镜像架构 {image_arch or '未知'} 与主机架构 "
            f"{host_facts.get('architecture', '未知')} 不兼容"
        )
    if image_os.lower() != "linux":
        raise ImageDistributionError(
            f"工作节点镜像操作系统必须是 linux，实际为 {image_os or '未知'}"
        )


def values(output: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)


def source_label(source: str) -> str:
    return {
        "existing-cache": "目标主机缓存",
        "private-registry": "私有仓库",
        "proxy": "镜像代理",
        "configured-registry": "镜像仓库",
        "ssh-transfer": "Seed SSH 传输",
    }.get(source, "镜像来源")


def safe_detail(value: str) -> str:
    value = re.sub(r"(?i)(password|token|private key)[^ ]*", "凭据", value or "")
    return " ".join(value.split())[:500] or "镜像分发失败"


def format_bytes(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{value} B"
