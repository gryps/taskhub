import json
import os
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path

_lock = threading.Lock()
_cache = None
_deadline = 0.0


def probe_browsers() -> dict:
    global _cache, _deadline
    with _lock:
        if _cache is None or time.monotonic() >= _deadline:
            _cache = _run_probe()
            _deadline = time.monotonic() + 30
        return _cache


def _run_probe() -> dict:
    names = ("windows_gui", "playwright", "chromium", "edge", "screenshot", "video", "trace")
    empty = {"capabilities": dict.fromkeys(names, False), "versions": {}}
    if os.name != "nt":
        return empty
    try:
        result = subprocess.run(
            [sys.executable, "-m", "taskhub_v2.node_agent.browser_probe"],
            capture_output=True, text=True, timeout=90, check=True,
        )
        return json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return empty


def browser_versions() -> dict[str, str]:
    versions = {"platform": platform.platform(), "python": platform.python_version()}
    versions.update(probe_browsers()["versions"])
    return versions


def browser_prerequisites() -> dict:
    profile = Path(os.getenv("TASKHUB_BROWSER_PROFILE_DIR", "")).expanduser()
    target = os.getenv("TASKHUB_BROWSER_AUTH_TARGET", "").strip()
    marker_value = os.getenv("TASKHUB_BROWSER_AUTH_READY_FILE", "").strip()
    marker = Path(marker_value).expanduser() if marker_value else None
    configured = bool(target and str(profile) not in {"", "."} and profile.is_dir())
    authenticated = bool(configured and marker and marker.is_file())
    configure_command = (
        "powershell -ExecutionPolicy Bypass -File "
        "C:\\TaskHub\\update-browser-prerequisites.ps1 "
        "-BrowserProfileDir '<专用Profile目录>' -BrowserAuthTarget '<授权登录地址>'"
    )
    authorize_command = ""
    if configured and not authenticated:
        safe_profile = str(profile).replace("'", "''")
        safe_target = target.replace("'", "''")
        authorize_command = (
            "powershell -ExecutionPolicy Bypass -File "
            "C:\\TaskHub\\authorize-browser-profile.ps1 -Browser edge "
            f"-ProfileDir '{safe_profile}' -AuthTarget '{safe_target}'"
        )
    return {
        "profile_configured": configured,
        "authenticated": authenticated,
        "target": target,
        "profile": str(profile) if str(profile) != "." else "",
        "action": (
            None if authenticated else {
                "label": "启动授权登录" if configured else "配置浏览器验收",
                "command": authorize_command or configure_command,
            }
        ),
        "detail": (
            "Profile 与授权登录均已就绪" if authenticated
            else "Profile 已配置，等待完成授权登录" if configured
            else "未配置专用浏览器 Profile 或授权目标"
        ),
    }
