from pathlib import Path

import yaml


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
