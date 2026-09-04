from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))
CONTROLLER = str(CONFIG["controller_url"]).rstrip("/")
TOKEN = str(CONFIG["worker_token"])
WORKER_ID = str(CONFIG.get("worker_id", "worker-31-34-gui"))
ARTIFACT_ROOT = ROOT / "artifacts"
MARKET_ROOT = Path(CONFIG.get("market_root", r"C:\Users\user\apps\douyin-market-automation"))
LISTING_ROOT = Path(os.path.expandvars(CONFIG.get("listing_root", r"%LOCALAPPDATA%\Gryps\Executor")))
STARTED_AT = time.time()
STOP = threading.Event()
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request_json(path: str, payload: dict[str, Any] | None = None) -> Any:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        f"{CONTROLLER}{path}", data=data,
        headers={"content-type": "application/json", "x-taskhub-worker-token": TOKEN, "x-worker-id": WORKER_ID},
        method="POST" if payload is not None else "GET",
    )
    with OPENER.open(request, timeout=20) as response:
        return json.load(response)


def validate_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    allowed = {str(item).lower() for item in CONFIG.get("allowed_hosts", [])}
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.hostname.lower() not in allowed:
        raise ValueError("URL host is not allowed by GUI worker configuration")
    return value


def _run(command: list[str], cwd: Path, timeout: int, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, check=False, env=env,
    )
    if completed.returncode:
        raise RuntimeError((completed.stderr or completed.stdout)[-2000:] or f"process exited {completed.returncode}")
    return completed.stdout.strip()


def run_h5_inspection(task: dict[str, Any]) -> dict[str, Any]:
    payload = task.get("input") if isinstance(task.get("input"), dict) else {}
    request_payload = {
        "url": validate_url(str(payload.get("url") or "")), "headed": payload.get("headed", True),
        "viewports": payload.get("viewports"), "required_selectors": payload.get("required_selectors", []),
        "timeout_ms": min(120000, max(5000, int(payload.get("timeout_ms", 60000)))),
    }
    task_dir = ARTIFACT_ROOT / str(task["id"])
    task_dir.mkdir(parents=True, exist_ok=True)
    input_path = task_dir / "request.json"
    input_path.write_text(json.dumps(request_payload, ensure_ascii=True, indent=2), encoding="utf-8")
    output = _run(["node", str(ROOT / "h5_acceptance.mjs"), str(input_path), str(task_dir)], ROOT, 180)
    report = json.loads(output)
    report.update({"worker": WORKER_ID, "artifact_root": str(task_dir)})
    if report.get("status") in {"failed", "error"}:
        raise RuntimeError("H5 acceptance failed")
    return report


def run_market_collection(task: dict[str, Any]) -> dict[str, Any]:
    if not (MARKET_ROOT / "src" / "douyin_market_automation").is_dir():
        raise RuntimeError("market collector is not installed")
    payload = task.get("input") or {}
    run_name = f"taskhub-{task['id']}"
    command = [
        sys.executable, "-m", "douyin_market_automation", "--adb", str(CONFIG.get("adb_path", r"C:\platform-tools\adb.exe")),
        "collect-products", "--keyword", str(payload["keyword"]), "--count", str(int(payload["count"])),
        "--max-scroll", str(min(100, max(1, int(payload.get("max_scroll", 30))))),
        "--max-gallery-images", str(min(10, max(1, int(payload.get("max_gallery_images", 4))))), "--run-name", run_name,
    ]
    env = {**os.environ, "PYTHONPATH": str(MARKET_ROOT / "src")}
    output = _run(command, MARKET_ROOT, min(14400, max(300, int(payload.get("timeout_seconds", 7200)))), env)
    report = json.loads(output)
    task_id = str(report.get("task_id") or report.get("id") or "")
    if not task_id:
        raise RuntimeError("collector output did not include task_id")
    artifacts = [str(path) for path in (MARKET_ROOT / "data").glob(f"**/*{task_id}*")][:100]
    return {"status": "collected", "task_id": task_id, "artifacts": artifacts, "summary": report}


def listing_ready() -> bool:
    return bool(
        LISTING_ROOT.is_dir() and (LISTING_ROOT / "current.txt").is_file()
        and CONFIG.get("listing_api_base_url") and CONFIG.get("listing_agent_id")
        and os.environ.get("DOUYIN_EXECUTOR_DEVICE_SECRET")
    )


def run_listing_draft(task: dict[str, Any]) -> dict[str, Any]:
    if not listing_ready():
        raise RuntimeError("listing executor is not paired")
    release = (LISTING_ROOT / "current.txt").read_text(encoding="utf-8").strip()
    release_root = LISTING_ROOT / "releases" / release
    python = release_root / ".venv" / "Scripts" / "python.exe"
    task_dir = ARTIFACT_ROOT / str(task["id"])
    task_dir.mkdir(parents=True, exist_ok=True)
    request_path = task_dir / "listing-request.json"
    request_path.write_text(json.dumps({
        "api_base_url": CONFIG["listing_api_base_url"], "agent_id": CONFIG["listing_agent_id"],
        "profile": str(LISTING_ROOT / "data" / "profile"), "artifact_dir": str(LISTING_ROOT / "data" / "artifacts"),
        "browser_channel": CONFIG.get("listing_browser_channel", "msedge"),
        "listing_task_id": str((task.get("input") or {}).get("listing_task_id")),
    }), encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(release_root / "source" / "apps" / "executor" / "src")}
    output = _run([str(python), str(ROOT / "listing_bridge.py"), str(request_path)], ROOT, 3600, env)
    result = json.loads(output.splitlines()[-1])
    return result


RUNNERS = {"h5.inspect": run_h5_inspection, "market.price.collect": run_market_collection, "commerce.listing.draft": run_listing_draft}


def enabled_types() -> list[str]:
    result = ["h5.inspect"]
    if MARKET_ROOT.is_dir():
        result.append("market.price.collect")
    if listing_ready():
        result.append("commerce.listing.draft")
    return result


def heartbeat(task_id: str, lease_token: str, done: threading.Event) -> None:
    while not done.wait(30):
        request_json(f"/taskhub/tasks/{task_id}/heartbeat", {"worker_id": WORKER_ID, "lease_token": lease_token})


def poll_loop() -> None:
    while not STOP.is_set():
        try:
            response = request_json("/taskhub/claim", {"worker_id": WORKER_ID, "project": CONFIG.get("project", "douyin-listing-workbench"), "types": enabled_types()})
            task = response.get("task")
            if not task:
                STOP.wait(2)
                continue
            done = threading.Event()
            threading.Thread(target=heartbeat, args=(task["id"], task["lease_token"], done), daemon=True).start()
            try:
                result = RUNNERS[task["type"]](task)
                request_json(f"/taskhub/tasks/{task['id']}/complete", {"worker_id": WORKER_ID, "lease_token": task["lease_token"], "result": result})
            except Exception as exc:
                request_json(f"/taskhub/tasks/{task['id']}/fail", {"worker_id": WORKER_ID, "lease_token": task["lease_token"], "error": {"code": type(exc).__name__, "message": str(exc)[-2000:]}})
            finally:
                done.set()
        except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
            STOP.wait(3)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/health":
            self.send_error(404)
            return
        payload = json.dumps({
            "status": "ok", "worker": WORKER_ID, "platform": "Windows GUI", "controller": CONTROLLER,
            "taskhub_poll": True, "task_types": ",".join(enabled_types()), "market_ready": MARKET_ROOT.is_dir(),
            "listing_ready": listing_ready(), "artifact_root": str(ARTIFACT_ROOT), "uptime_seconds": round(time.time() - STARTED_AT, 3),
        }).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


if __name__ == "__main__":
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=poll_loop, daemon=True).start()
    server = ThreadingHTTPServer((str(CONFIG.get("host", "0.0.0.0")), int(CONFIG.get("port", 8125))), HealthHandler)
    try:
        server.serve_forever()
    finally:
        STOP.set()
        server.server_close()
