import asyncio
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any


class DeviceAuthError(RuntimeError):
    pass


class CodexDeviceAuthService:
    def __init__(self, codex_bin: str, account_root: str, proxy_url: str = ""):
        self.codex_bin = codex_bin
        self.account_root = Path(account_root)
        self.proxy_url = proxy_url
        self.sessions: dict[str, dict[str, Any]] = {}

    async def start(self, model_id: str) -> dict[str, Any]:
        binary = shutil.which(self.codex_bin) if "/" not in self.codex_bin else self.codex_bin
        if not binary or not Path(binary).is_file():
            raise DeviceAuthError("Seed 控制器尚未安装 Codex CLI，无法启动账号授权")
        for session in self.sessions.values():
            if session["model_id"] == model_id and session["status"] in {"starting", "waiting"}:
                return self._public(session)
        home = self.account_root / model_id
        home.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(home, 0o700)
        session = {
            "session_id": uuid.uuid4().hex,
            "model_id": model_id,
            "status": "starting",
            "login_url": "https://auth.openai.com/codex/device",
            "device_code": "",
            "detail": "正在请求设备验证码",
            "started_at": time.time(),
        }
        self.sessions[session["session_id"]] = session
        session["task"] = asyncio.create_task(self._run(session, str(binary), home))
        for _ in range(20):
            if session["status"] != "starting":
                break
            await asyncio.sleep(0.1)
        return self._public(session)

    def status(self, session_id: str) -> dict[str, Any]:
        session = self.sessions.get(session_id)
        if not session:
            raise DeviceAuthError("授权会话不存在或已过期")
        return self._public(session)

    async def _run(self, session: dict[str, Any], binary: str, home: Path) -> None:
        env = dict(os.environ)
        env["CODEX_HOME"] = str(home)
        if self.proxy_url:
            env.update({"HTTP_PROXY": self.proxy_url, "HTTPS_PROXY": self.proxy_url})
        try:
            process = await asyncio.create_subprocess_exec(
                binary,
                "login",
                "--device-auth",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
            session["process"] = process
            output = ""
            assert process.stdout is not None
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                output = (output + line.decode("utf-8", errors="replace"))[-4000:]
                code = re.search(r"\b[A-Z0-9]{4}(?:-[A-Z0-9]{4})+\b", output)
                url = re.search(r"https://[^\s]+", output)
                if code:
                    session.update(
                        status="waiting",
                        device_code=code.group(0),
                        login_url=(url.group(0).rstrip(".,)") if url else session["login_url"]),
                        detail="请在登录页面输入验证码并完成授权",
                    )
            return_code = await process.wait()
            auth_file = home / "auth.json"
            if return_code == 0 and auth_file.is_file():
                os.chmod(auth_file, 0o600)
                session.update(status="authenticated", detail="账号授权成功，令牌已安全保存")
            else:
                safe = re.sub(r"[A-Za-z0-9_-]{24,}", "[redacted]", output)
                session.update(status="failed", detail=(safe.strip()[-300:] or "账号授权失败"))
        except (OSError, asyncio.CancelledError) as exc:
            session.update(status="failed", detail=f"无法启动账号授权：{type(exc).__name__}")

    @staticmethod
    def _public(session: dict[str, Any]) -> dict[str, Any]:
        return {
            key: session[key]
            for key in (
                "session_id", "model_id", "status", "login_url", "device_code", "detail",
                "started_at",
            )
        }
