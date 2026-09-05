from __future__ import annotations

import json
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlencode


ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))
CONTROLLER = str(CONFIG["controller_url"]).rstrip("/")
TOKEN = str(CONFIG["worker_token"])
WORKER_ID = str(CONFIG.get("worker_id", "worker-31-34-gui"))
ARTIFACT_ROOT = ROOT / "artifacts"
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
    if payload.get("allowed_host_confirmed") is not True:
        raise ValueError("GUI inspection host was not confirmed by a human")
    if payload.get("login_environment_confirmed") is not True:
        raise ValueError("GUI inspection login environment was not confirmed by a human")
    profile_path = str(CONFIG.get("profile_path") or "").strip()
    if payload.get("login_environment") == "dedicated_profile" and not profile_path:
        raise ValueError("dedicated GUI profile is not configured")
    request_payload = {
        "url": validate_url(str(payload.get("url") or "")), "headed": payload.get("headed", True),
        "viewports": payload.get("viewports"), "required_selectors": payload.get("required_selectors", []),
        "timeout_ms": min(120000, max(5000, int(payload.get("timeout_ms", 60000)))),
        "profile_dir": profile_path if payload.get("login_environment") == "dedicated_profile" else None,
        "forbidden_path_prefixes": payload.get("forbidden_path_prefixes", ["/login"]),
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


RUNNERS = {"h5.inspect": run_h5_inspection}


def publish_artifacts(task: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    published = []
    for value in result.get("artifacts", [])[:100]:
        path = Path(str(value))
        candidates = [path] if path.is_file() else list(path.glob("**/*"))[:100] if path.is_dir() else []
        for candidate in candidates:
            if not candidate.is_file() or candidate.stat().st_size > 20 * 1024 * 1024:
                continue
            artifact_id = str(uuid.uuid4())
            query = urlencode({
                "project": task.get("project", CONFIG.get("project", "douyin-listing-workbench")),
                "task_id": task["id"], "name": candidate.name, "source_path": str(candidate),
            })
            request = urllib.request.Request(
                f"{CONTROLLER}/taskhub/integration/artifacts/{artifact_id}/content?{query}",
                data=candidate.read_bytes(), method="PUT",
                headers={"content-type": "application/octet-stream", "x-taskhub-worker-token": TOKEN, "x-worker-id": WORKER_ID},
            )
            with OPENER.open(request, timeout=90) as response:
                published.append(json.load(response))
    return published


def enabled_types() -> list[str]:
    return ["h5.inspect"]


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
                result["taskhub_artifacts"] = publish_artifacts(task, result)
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
            "taskhub_poll": True, "task_types": ",".join(enabled_types()),
            "artifact_root": str(ARTIFACT_ROOT), "uptime_seconds": round(time.time() - STARTED_AT, 3),
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
