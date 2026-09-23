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
