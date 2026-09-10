import asyncio
import os
import subprocess
import sys
from pathlib import Path

from taskhub_v2.node_agent.runtime import PROXY_VARIABLES, normalize_command


async def prepare_browser_dependencies(
    target: Path, commands: list[list[str]], required_capabilities: set[str], timeout: int
) -> list[dict]:
    if "playwright" not in required_capabilities:
        return []
    if not (target / "package.json").is_file() or (target / "node_modules").is_dir():
        return []
    if not any(
        Path(command[0]).name.lower() in {"npx", "npx.cmd"} for command in commands if command
    ):
        return []
    command = ["npm", "ci"] if (target / "package-lock.json").is_file() else ["npm", "install"]
    command.extend(["--ignore-scripts", "--no-audit", "--no-fund"])

    def install() -> dict:
        environment = dict(os.environ)
        environment["PATH"] = os.pathsep.join(
            (str(Path(sys.executable).parent), environment.get("PATH", ""))
        )
        for key in PROXY_VARIABLES:
            environment.pop(key, None)
        try:
            result = subprocess.run(
                normalize_command(command),
                cwd=target,
                env=environment,
                capture_output=True,
                text=True,
                timeout=min(max(timeout, 60), 600),
            )
            output = (result.stdout + result.stderr)[-32_000:]
            return {"command": command, "exit_code": result.returncode, "output_tail": output}
        except FileNotFoundError:
            return {"command": command, "exit_code": 127, "output_tail": "command not found"}
        except TimeoutError:
            return {"command": command, "exit_code": 124, "output_tail": "command timed out"}

    return [await asyncio.to_thread(install)]
