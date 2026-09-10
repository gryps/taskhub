from __future__ import annotations

import http.client
import json
import socket
from typing import BinaryIO


class DockerUnavailableError(RuntimeError):
    pass


class UnixSocketConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str):
        super().__init__("localhost")
        self.socket_path = socket_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.socket_path)


class DockerSocketClient:
    def __init__(self, socket_path: str):
        self.socket_path = socket_path

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
        status, raw = self.request_bytes(method, path, payload)
        data = json.loads(raw) if raw else {}
        return status, data

    def request_bytes(
        self, method: str, path: str, payload: dict | None = None
    ) -> tuple[int, bytes]:
        connection = UnixSocketConnection(self.socket_path)
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read()
        except (OSError, http.client.HTTPException) as exc:
            raise DockerUnavailableError(f"Docker Engine 不可用：{exc}") from exc
        finally:
            connection.close()
        return response.status, raw

    def download(self, path: str, destination: BinaryIO) -> None:
        connection = UnixSocketConnection(self.socket_path)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            if response.status != 200:
                raw = response.read()
                try:
                    detail = json.loads(raw).get("message", "无法导出镜像")
                except (json.JSONDecodeError, UnicodeDecodeError):
                    detail = "无法导出镜像"
                raise DockerUnavailableError(detail)
            while chunk := response.read(1024 * 1024):
                destination.write(chunk)
        except (OSError, http.client.HTTPException) as exc:
            raise DockerUnavailableError(f"Docker Engine 导出镜像失败：{exc}") from exc
        finally:
            connection.close()

    def upload(self, path: str, source: BinaryIO, length: int) -> list[dict]:
        connection = UnixSocketConnection(self.socket_path)
        try:
            connection.request(
                "POST",
                path,
                body=source,
                headers={
                    "Content-Type": "application/x-tar",
                    "Content-Length": str(length),
                },
            )
            response = connection.getresponse()
            raw = response.read()
        except (OSError, http.client.HTTPException) as exc:
            raise DockerUnavailableError(f"Docker Engine 导入镜像失败：{exc}") from exc
        finally:
            connection.close()
        messages = []
        for line in raw.splitlines():
            try:
                messages.append(json.loads(line))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
        error = next(
            (
                item.get("error") or (item.get("errorDetail") or {}).get("message")
                for item in messages
                if item.get("error") or item.get("errorDetail")
            ),
            "",
        )
        if response.status != 200 or error:
            detail = error or f"Docker Engine 导入镜像失败：HTTP {response.status}"
            raise DockerUnavailableError(detail)
        return messages
