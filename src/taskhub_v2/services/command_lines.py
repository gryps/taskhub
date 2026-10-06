from __future__ import annotations

import shlex


def parse_command_lines(value: str, *, label: str = "命令") -> list[list[str]]:
    """Parse one shell-style argv command per line without invoking a shell."""
    commands: list[list[str]] = []
    for line in value.splitlines():
        if not line.strip():
            continue
        try:
            commands.append(shlex.split(line))
        except ValueError as exc:
            raise ValueError(f"{label}格式错误：{exc}") from exc
    return commands


def format_command_lines(commands: list[list[str]]) -> str:
    return "\n".join(shlex.join(command) for command in commands)
