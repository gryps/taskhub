#!/usr/bin/env python3
"""Verify a TaskHub development-agent connection without exposing its credential."""

import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

from taskhub_client import build_http_opener, request_options  # noqa: E402

DEFAULT_CONNECTION_FILE = Path.home() / ".codex" / "taskhub-v2-connection.json"


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--connection-file",
        default=str(DEFAULT_CONNECTION_FILE),
        help="Non-secret TaskHub connection JSON",
    )
    parser.add_argument("--timeout-seconds", type=float, default=10)
    return parser.parse_args()


def load_connection(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("connection file must contain a JSON object")
    if not value.get("health_url"):
        raise ValueError(
            "connection file is missing health_url; endpoint paths must not be guessed"
        )
    return value


def fetch_json(opener, url: str, headers: dict[str, str], timeout_seconds: float) -> dict:
    request = Request(url, headers=headers)
    with opener.open(request, timeout=max(1, timeout_seconds)) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("TaskHub endpoint returned a non-object JSON response")
    return payload


def check_connection(path: Path, timeout_seconds: float) -> dict:
    connection = load_connection(path)
    ca_file, trust_env, headers = request_options(connection)
    result = {"connection": "present", "health": "unavailable", "authentication": "not-checked"}
    opener = build_http_opener(ca_file, trust_env)
    health_payload = fetch_json(
        opener, str(connection["health_url"]), headers, timeout_seconds
    )
    if health_payload.get("status") != "ok":
        raise RuntimeError("health endpoint did not report status=ok")
    result["health"] = "ok"
    auth_url = connection.get("auth_status_url")
    if auth_url:
        auth_payload = fetch_json(opener, str(auth_url), headers, timeout_seconds)
        result["authentication"] = (
            "authenticated" if auth_payload.get("authenticated") else "unauthenticated"
        )
        result["role"] = auth_payload.get("role")
    return result


def main() -> int:
    args = arguments()
    try:
        result = check_connection(Path(args.connection_file).expanduser(), args.timeout_seconds)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError, HTTPError, URLError) as error:
        print(f"TaskHub connection unavailable: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
