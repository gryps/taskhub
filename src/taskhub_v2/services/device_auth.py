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
    def __init__(
        self,
        codex_bin: str,
        account_root: str,
        proxy_url: str = "",
        startup_timeout_seconds: float = 20,
        authorization_timeout_seconds: float = 900,
    ):
        self.codex_bin = codex_bin
        self.account_root = Path(account_root)
        self.proxy_url = proxy_url
        self.startup_timeout_seconds = startup_timeout_seconds
        self.authorization_timeout_seconds = authorization_timeout_seconds
        self.sessions: dict[str, dict[str, Any]] = {}

    async def start(self, model_id: str, proxy_url: str = "") -> dict[str, Any]:
        binary = shutil.which(self.codex_bin) if "/" not in self.codex_bin else self.codex_bin
        if not binary or not Path(binary).is_file():
            raise DeviceAuthError("Seed 控制器尚未安装 Codex CLI，无法启动账号授权")
        for session in self.sessions.values():
            if session["model_id"] != model_id or session["status"] not in {
                "starting",
                "waiting",
            }:
                continue
            task = session.get("task")
            if task and not task.done():
                task.cancel()
                await task
            session.update(status="superseded", detail="已由新的设备授权会话替代")
        home = self.account_root / model_id
        home.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(home, 0o700)
        session = {
            "session_id": uuid.uuid4().hex,
            "model_id": model_id,
            "status": "starting",
            "login_url": "",
            "device_code": "",
            "detail": "正在请求设备验证码",
            "started_at": time.time(),
            "proxy_url": proxy_url or self.proxy_url,
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
        if session["proxy_url"]:
            env.update({"HTTP_PROXY": session["proxy_url"], "HTTPS_PROXY": session["proxy_url"]})
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
                timeout = (
                    self.startup_timeout_seconds
                    if session["status"] == "starting"
                    else self.authorization_timeout_seconds
                )
                try:
                    chunk = await asyncio.wait_for(process.stdout.read(512), timeout=timeout)
                except TimeoutError:
                    await self._terminate(process)
                    if session["status"] == "starting":
                        session.update(
                            status="failed",
                            detail=(
                                "未能从 OpenAI 获取设备验证码。请检查 Seed 的外网或代理，"
                                "并确认 ChatGPT 安全设置或工作区权限已启用设备码登录"
                            ),
                        )
                    else:
                        session.update(status="failed", detail="设备授权已超时，请重新发起授权")
                    return
                if not chunk:
                    break
                output = (output + chunk.decode("utf-8", errors="replace"))[-4000:]
                clean_output = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)
                code = re.search(
                    r"one-time code[^\r\n]*(?:\r?\n)+\s*([A-Z0-9]{4,8}(?:-[A-Z0-9]{4,8})+)",
                    clean_output,
                    re.IGNORECASE,
                ) or re.search(
                    r"(?m)^\s*((?=[A-Z0-9-]*\d)[A-Z0-9]{4,8}(?:-[A-Z0-9]{4,8})+)\s*$",
                    clean_output,
                    re.IGNORECASE,
                )
                url = re.search(r"https://[^\s]+", clean_output)
                login_url = url.group(0).rstrip(".,)") if url else session["login_url"]
                if url:
                    session["login_url"] = login_url
                if code and login_url:
                    session.update(
                        status="waiting",
                        device_code=code.group(1).upper(),
                        login_url=login_url,
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
            process = session.get("process")
            if process:
                await self._terminate(process)
            session.update(status="failed", detail=f"无法启动账号授权：{type(exc).__name__}")

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=3)
        except TimeoutError:
            process.kill()
            await process.wait()

    @staticmethod
    def _public(session: dict[str, Any]) -> dict[str, Any]:
        return {
            key: session[key]
            for key in (
                "session_id", "model_id", "status", "login_url", "device_code", "detail",
                "started_at",
            )
        }
