"""Shared authenticated HTTP client for TaskHub release helpers."""

from __future__ import annotations

import json
import ssl
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener


class TaskHubClientError(RuntimeError):
    """A safe, user-facing TaskHub client error."""


def load_connection(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("connection file must contain a JSON object")
    if not value.get("base_url"):
        raise ValueError("connection file is missing base_url")
    return value


def request_options(connection: dict) -> tuple[str | None, bool, dict[str, str]]:
    base_url = str(connection["base_url"])
    ca_file = connection.get("tls_ca_file")
    if base_url.startswith("https://"):
        if not ca_file:
            raise ValueError("HTTPS connection is missing tls_ca_file")
        if not Path(ca_file).is_file():
            raise ValueError("tls_ca_file does not exist")
    headers = {"Accept": "application/json"}
    if connection.get("api_auth") == "bearer-token":
        token_path = connection.get("agent_token_file")
        if not token_path:
            raise ValueError("bearer-token connection is missing agent_token_file")
        token_file = Path(token_path)
        if not token_file.is_file():
            raise ValueError("agent_token_file does not exist")
        token = token_file.read_text(encoding="utf-8").strip()
        if not token:
            raise ValueError("agent_token_file is empty")
        headers["Authorization"] = f"Bearer {token}"
    return str(ca_file) if ca_file else None, connection.get("proxy_mode") != "direct", headers


def build_http_opener(ca_file: str | None, trust_env: bool):
    context = ssl.create_default_context(cafile=ca_file)
    proxy_handler = ProxyHandler() if trust_env else ProxyHandler({})
    return build_opener(proxy_handler, HTTPSHandler(context=context))


class TaskHubClient:
    def __init__(self, connection: dict, timeout_seconds: float = 15, opener=None):
        ca_file, trust_env, self.headers = request_options(connection)
        self.base_url = str(connection["base_url"]).rstrip("/") + "/"
        self.timeout_seconds = max(1, timeout_seconds)
        self.opener = opener or build_http_opener(ca_file, trust_env)

    @classmethod
    def from_file(cls, path: Path, timeout_seconds: float = 15):
        return cls(load_connection(path), timeout_seconds)

    def request(self, method: str, path: str, *, query: dict | None = None, body=None):
        endpoint = path.lstrip("/")
        url = urljoin(self.base_url, endpoint)
        if query:
            values = {key: value for key, value in query.items() if value is not None}
            url = f"{url}?{urlencode(values)}"
        headers = dict(self.headers)
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout_seconds) as response:
                payload = json.load(response)
        except HTTPError as error:
            detail = f"HTTP {error.code}"
            try:
                payload = json.loads(error.read().decode("utf-8"))
                if isinstance(payload, dict) and payload.get("detail"):
                    detail = f"{detail}: {payload['detail']}"
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
            raise TaskHubClientError(detail) from error
        if not isinstance(payload, (dict, list)):
            raise TaskHubClientError("TaskHub endpoint returned unsupported JSON")
        return payload

    def get(self, path: str, *, query: dict | None = None):
        return self.request("GET", path, query=query)

    def post(self, path: str, body: dict):
        return self.request("POST", path, body=body)
