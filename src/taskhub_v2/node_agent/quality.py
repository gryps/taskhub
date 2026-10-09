from pathlib import Path

from taskhub_v2.node_agent.browser_dependencies import prepare_browser_dependencies
from taskhub_v2.node_agent.runtime import run_commands


async def prepare_quality_workspace(
    target: Path,
    setup_commands: list[list[str]],
    quality_commands: list[list[str]],
    required_capabilities: set[str],
    timeout_seconds: int,
    environment: dict[str, str],
) -> list[dict]:
    tests = []
    if setup_commands:
        tests = await run_commands(
            target,
            setup_commands,
            timeout_seconds,
            execution_environment=environment,
        )
    if not any(test["exit_code"] for test in tests):
        tests.extend(
            await prepare_browser_dependencies(
                target,
                quality_commands,
                required_capabilities,
                timeout_seconds,
            )
        )
    return tests


async def complete_quality_workspace(
    tests: list[dict],
    target: Path,
    commands: list[list[str]],
    timeout_seconds: int,
    environment: dict[str, str],
) -> None:
    if any(test["exit_code"] for test in tests):
        return
    tests.extend(
        await run_commands(
            target,
            commands,
            timeout_seconds,
            execution_environment=environment,
        )
    )
