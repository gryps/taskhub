import hashlib
from collections.abc import Awaitable, Callable
from pathlib import Path

GENERATED_PARTS = {".venv", "__pycache__", ".pytest_cache", "node_modules", "dist", "build"}
GENERATED_SUFFIXES = {".pyc", ".pyo", ".coverage"}


def is_generated(name: str) -> bool:
    path = Path(name)
    return (
        bool(GENERATED_PARTS.intersection(path.parts))
        or path.suffix in GENERATED_SUFFIXES
        or path.name == ".coverage"
    )


def no_change_feedback(model_result, existing_feedback: str) -> str:
    prefix = f"{existing_feedback}\n" if existing_feedback else ""
    return (
        prefix + "The previous coding attempt returned successfully but produced no Git "
        "changes. Its summary was: "
        + model_result.content.summary
        + ". Re-inspect the repository, identify the concrete files required by the "
        "approved plan, implement the missing behavior now, and verify that `git status "
        "--short` lists the edits before returning. Do not merely describe or validate "
        "the existing implementation."
    )


async def worktree_fingerprint(
    workdir: str,
    git: Callable[..., Awaitable[str]],
    is_generated: Callable[[str], bool],
) -> str:
    """Detect whether a model changed an already-dirty worktree."""
    status = await git(workdir, "status", "--porcelain=v1", "-z")
    diff = await git(workdir, "diff", "--binary", "--no-ext-diff")
    digest = hashlib.sha256()
    digest.update(status.encode())
    digest.update(diff.encode())
    for name in sorted(
        line[3:].strip() for line in status.split("\0") if line.startswith("?? ")
    ):
        path = Path(workdir, name)
        digest.update(name.encode())
        if path.is_file() and not is_generated(name):
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def feedback_key(feedback: str) -> str:
    return hashlib.sha256(feedback.encode()).hexdigest()[:16]


def artifact_name(revision: int) -> str:
    return "change.patch" if revision == 0 else f"change-r{revision}.patch"
