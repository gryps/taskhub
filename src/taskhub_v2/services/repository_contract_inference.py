from __future__ import annotations

import re
from pathlib import Path

from taskhub_v2.domain.project_contract import ModuleContract

DATA_DIRECTORY_NAMES = {"api", "backend", "data", "database", "infrastructure", "server"}


def detect_repository_profile(repository: Path) -> str:
    if (repository / "frontend").is_dir() and (repository / "backend").is_dir():
        return "fullstack-web"
    if _has_nested_manifest(repository, "package.json") and _has_nested_manifest(
        repository, "pyproject.toml"
    ):
        return "fullstack-web"
    package = repository / "package.json"
    pyproject = repository / "pyproject.toml"
    if package.is_file() and not pyproject.is_file():
        return "frontend-spa"
    if any((repository / name).exists() for name in ("worker.py", "celery.py", "src/worker")):
        return "worker-service"
    if any((repository / name).exists() for name in ("openapi.yaml", "openapi.json", "src/api")):
        return "backend-api"
    if package.is_file() and pyproject.is_file() and _package_has_build(package):
        return "fullstack-web"
    return "python-service"


def infer_repository_modules(
    repository: Path, profile_id: str, defaults: list[ModuleContract]
) -> list[ModuleContract]:
    """Materialize profile modules against an existing repository layout.

    Built-in profiles describe conventional single-package layouts. Existing projects
    frequently use ``apps/*`` or ``packages/*`` roots, so persisting those templates
    verbatim creates contracts for paths that do not exist. Only replace the defaults
    when repository evidence identifies concrete source roots.
    """

    source_roots = _source_roots(repository, profile_id)
    if not source_roots:
        return [item.model_copy(deep=True) for item in defaults]

    modules: list[ModuleContract] = []
    used_names: set[str] = set()
    for root in source_roots:
        relative = root.relative_to(repository).as_posix()
        name = _unique_name(_module_name(root), used_names)
        used_names.add(name)
        modules.append(
            ModuleContract(
                name=name,
                paths=[f"{relative}/**"],
                allow_data_access=name in DATA_DIRECTORY_NAMES,
            )
        )

    docs = repository / "docs"
    if docs.is_dir():
        name = _unique_name("documentation", used_names)
        modules.append(ModuleContract(name=name, paths=["docs/**"]))
    return modules


def inferred_directory_structure(modules: list[ModuleContract]) -> list[str]:
    return sorted({path.split("/")[0] for module in modules for path in module.paths})


def _source_roots(repository: Path, profile_id: str) -> list[Path]:
    node_roots = _manifest_source_roots(repository, "package.json")
    python_roots = _python_source_roots(repository)
    if profile_id == "frontend-spa":
        roots = node_roots
    elif profile_id == "fullstack-web":
        roots = [*node_roots, *python_roots]
    else:
        roots = python_roots
    return _without_nested_duplicates(roots)


def _manifest_source_roots(repository: Path, manifest: str) -> list[Path]:
    roots = []
    for file in repository.glob(f"**/{manifest}"):
        if _ignored(file.relative_to(repository).parts):
            continue
        source = file.parent / "src"
        if source.is_dir():
            roots.append(source)
    return roots


def _has_nested_manifest(repository: Path, manifest: str) -> bool:
    return any(
        file.parent != repository and not _ignored(file.relative_to(repository).parts)
        for file in repository.glob(f"**/{manifest}")
    )


def _package_has_build(package: Path) -> bool:
    import json

    try:
        payload = json.loads(package.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(payload.get("scripts", {}).get("build"))


def _python_source_roots(repository: Path) -> list[Path]:
    roots = []
    for file in repository.glob("**/pyproject.toml"):
        if _ignored(file.relative_to(repository).parts):
            continue
        candidates = (file.parent / "src", file.parent / "app")
        root = next((item for item in candidates if item.is_dir()), None)
        if root is not None:
            roots.append(root)
    return roots


def _without_nested_duplicates(paths: list[Path]) -> list[Path]:
    return sorted(set(paths), key=lambda item: item.as_posix())


def _module_name(source_root: Path) -> str:
    owner = source_root.parent.name if source_root.name in {"src", "app"} else source_root.name
    value = re.sub(r"[^a-z0-9_-]+", "-", owner.casefold()).strip("-_")
    return value or "source"


def _unique_name(name: str, used: set[str]) -> str:
    if name not in used:
        return name
    index = 2
    while f"{name}-{index}" in used:
        index += 1
    return f"{name}-{index}"


def _ignored(parts: tuple[str, ...]) -> bool:
    return bool({".git", ".venv", "node_modules", "dist", "build"}.intersection(parts))
