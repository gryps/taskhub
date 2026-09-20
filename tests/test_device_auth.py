import asyncio
from pathlib import Path

from taskhub_v2.services.device_auth import CodexDeviceAuthService


def test_device_auth_reports_startup_timeout(tmp_path: Path):
    executable = tmp_path / "codex-hang"
    executable.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
    executable.chmod(0o755)
    service = CodexDeviceAuthService(
        str(executable),
        str(tmp_path / "accounts"),
        startup_timeout_seconds=0.05,
    )

    result = asyncio.run(service.start("openai-account"))

    assert result["status"] == "failed"
    assert "未能从 OpenAI 获取设备验证码" in result["detail"]


def test_device_auth_uses_model_card_proxy(tmp_path: Path):
    executable = tmp_path / "codex-proxy"
    executable.write_text(
        "#!/bin/sh\n"
        'test "$HTTPS_PROXY" = "http://proxy.example:7893" || exit 2\n'
        'echo "Open https://auth.openai.com/codex/device with code ABCD-EFGH"\n'
        'touch "$CODEX_HOME/auth.json"\n',
        encoding="utf-8",
    )
    executable.chmod(0o755)
    service = CodexDeviceAuthService(str(executable), str(tmp_path / "accounts"))

    async def scenario():
        result = await service.start(
            "openai-account", proxy_url="http://proxy.example:7893"
        )
        for _ in range(20):
            result = service.status(result["session_id"])
            if result["status"] == "authenticated":
                break
            await asyncio.sleep(0.01)
        return result

    result = asyncio.run(scenario())

    assert result["status"] == "authenticated"


def test_device_auth_parses_cli_output_without_trailing_newline(tmp_path: Path):
    executable = tmp_path / "codex-no-newline"
    executable.write_text(
        "#!/bin/sh\n"
        'printf "COMMAND-LINE login\\nOpen https://auth.openai.com/codex/device\\n"\n'
        'printf "Enter this one-time code (expires in 15 minutes)\\nwxyz-12345"\n'
        "sleep 1\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    service = CodexDeviceAuthService(str(executable), str(tmp_path / "accounts"))

    result = asyncio.run(service.start("openai-account"))

    assert result["status"] == "waiting"
    assert result["login_url"] == "https://auth.openai.com/codex/device"
    assert result["device_code"] == "WXYZ-12345"


def test_device_auth_does_not_treat_cli_labels_as_codes(tmp_path: Path):
    executable = tmp_path / "codex-label-only"
    executable.write_text(
        "#!/bin/sh\n"
        'printf "COMMAND-LINE login\\nOpen https://auth.openai.com/codex/device"\n'
        "sleep 30\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    service = CodexDeviceAuthService(
        str(executable),
        str(tmp_path / "accounts"),
        startup_timeout_seconds=0.05,
    )

    result = asyncio.run(service.start("openai-account"))

    assert result["status"] == "failed"
    assert result["device_code"] == ""


def test_new_device_auth_supersedes_waiting_session(tmp_path: Path):
    executable = tmp_path / "codex-renew"
    executable.write_text(
        "#!/bin/sh\n"
        'printf "Open https://auth.openai.com/codex/device\\n"\n'
        'printf "Enter this one-time code\\nABCD-12345"\n'
        "sleep 30\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    service = CodexDeviceAuthService(str(executable), str(tmp_path / "accounts"))

    async def scenario():
        first = await service.start("openai-account")
        second = await service.start("openai-account")
        first_status = service.status(first["session_id"])
        task = service.sessions[second["session_id"]]["task"]
        task.cancel()
        await task
        return first, second, first_status

    first, second, first_status = asyncio.run(scenario())

    assert first["session_id"] != second["session_id"]
    assert first_status["status"] == "superseded"
