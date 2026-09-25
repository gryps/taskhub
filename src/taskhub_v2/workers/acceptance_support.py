import subprocess
from pathlib import Path

import yaml


def authorized_windows_nodes(project) -> set[str]:
    """Return only controller-owned Windows nodes explicitly granted to a project."""
    if project.windows_acceptance_node_ids:
        return set(project.windows_acceptance_node_ids)
    return set(project.windows_test_suite.node_ids) if project.windows_test_suite else set()


def browser_acceptance_commit(worktree: str, expected_commit: str) -> str | None:
    """Resolve the tested HEAD when it is the expected commit or its descendant."""
    actual_commit = subprocess.run(
        ["git", "-C", worktree, "rev-parse", "--verify", "HEAD^{commit}"],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    ).stdout.strip()
    if actual_commit == expected_commit:
        return actual_commit
    ancestor = subprocess.run(
        ["git", "-C", worktree, "merge-base", "--is-ancestor", expected_commit, actual_commit],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return actual_commit if ancestor.returncode == 0 else None


def is_browser_contract(path: Path) -> bool:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return True
    return not isinstance(payload, dict) or bool(
        {"preview", "preproduction", "workload"} & payload.keys()
    )


def artifact_kind(path: str) -> str:
    if path.endswith("trace.zip"):
        return "browser_trace"
    if path.endswith(".xml"):
        return "junit_report"
    if path.lower().endswith((".png", ".jpg", ".jpeg")):
        return "screenshot"
    if path.lower().endswith((".webm", ".mp4")):
        return "browser_video"
    return "playwright_report"


def browser_display_names(browsers: list[str]) -> str:
    """Describe the system browser channels used by Windows acceptance."""
    labels = {
        "chromium": "system Google Chrome",
        "edge": "system Microsoft Edge",
    }
    return " and ".join(labels.get(browser, browser) for browser in browsers)


def browser_acceptance_summary(
    browsers: list[str], target_url: str, commit: str, scenario_ids: list[str]
) -> str:
    return (
        f"{browser_display_names(browsers)} acceptance at {target_url} for {commit}; "
        "zero failures and skips; verified scenarios: " + ", ".join(scenario_ids)
    )
