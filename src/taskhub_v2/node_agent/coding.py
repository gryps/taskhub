import hashlib
import io
import json
import os
import tarfile
from functools import lru_cache
from pathlib import Path

from taskhub_v2.config import get_settings
from taskhub_v2.domain.models import Plan
from taskhub_v2.providers.health import ProviderHealthStore
from taskhub_v2.workers.factory import build_coder

FORBIDDEN_NAMES = {".env", ".env.local", "auth.json", "credentials.json"}
EXCLUDED_PARTS = {".git", "__pycache__", ".pytest_cache", "node_modules", "dist", "build"}


def coding_available() -> bool:
    settings = get_settings()
    if os.getenv("TASKHUB_NODE_CODING_ENABLED", "false").lower() != "true":
        return False
    return Path(settings.codex_cli_bin).is_file() and (
        Path(settings.codex_plus_home, "auth.json").is_file()
        or Path(settings.codex_pro_home, "auth.json").is_file()
        or bool(settings.gpt_api_key)
    )


async def modify_workspace(
    workdir: Path, requirement: str, plan: Plan, feedback: str
) -> bytes:
    before = snapshot(workdir)
    result = await node_coder().modify(
        requirement, plan, str(workdir), feedback=feedback
    )
    after = snapshot(workdir)
    changed = sorted(name for name, digest in after.items() if before.get(name) != digest)
    deleted = sorted(name for name in before if name not in after)
    manifest = {
        "summary": result.content.summary,
        "tests": result.content.tests,
        "provider": result.provider,
        "model": result.model,
        "duration_ms": result.duration_ms,
        "failed_providers": result.failed_providers,
        "changed_files": changed,
        "deleted_files": deleted,
    }
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as bundle:
        payload = json.dumps(manifest).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(payload)
        bundle.addfile(info, io.BytesIO(payload))
        for name in changed:
            bundle.add(workdir / name, arcname=f"files/{name}", recursive=False)
    return output.getvalue()


@lru_cache
def node_health_store() -> ProviderHealthStore:
    settings = get_settings()
    return ProviderHealthStore(
        settings.provider_health_file,
        settings.provider_quota_cooldown_seconds,
        settings.provider_transient_cooldown_seconds,
    )


@lru_cache
def node_coder():
    return build_coder(get_settings(), node_health_store())


def provider_health() -> dict:
    return node_health_store().snapshot() if coding_available() else {}


def snapshot(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if excluded(relative) or path.is_symlink() or not path.is_file():
            continue
        result[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def excluded(path: Path) -> bool:
    return (
        bool(EXCLUDED_PARTS.intersection(path.parts))
        or path.name in FORBIDDEN_NAMES
        or path.name.startswith(".env.")
        or path.name in {".coverage"}
        or path.suffix in {".pyc", ".pyo"}
    )
