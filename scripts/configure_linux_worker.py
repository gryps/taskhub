from __future__ import annotations

import argparse
from pathlib import Path


REQUIRED_TASK_TYPES = {"code.change", "code.diff.preview", "code.change.apply", "test.run", "quality.env.check"}


def update_env(path: Path, workspace_id: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    output: list[str] = []
    found_workspace = False
    found_types = False
    for line in lines:
        if line.startswith("PROJECT_WORKSPACE_ID="):
            output.append(f"PROJECT_WORKSPACE_ID={workspace_id}")
            found_workspace = True
        elif line.startswith("WORKER_TASK_TYPES="):
            current = {item.strip() for item in line.partition("=")[2].split(",") if item.strip()}
            output.append("WORKER_TASK_TYPES=" + ",".join(sorted(current | REQUIRED_TASK_TYPES)))
            found_types = True
        else:
            output.append(line)
    if not found_workspace:
        output.append(f"PROJECT_WORKSPACE_ID={workspace_id}")
    if not found_types:
        output.append("WORKER_TASK_TYPES=" + ",".join(sorted(REQUIRED_TASK_TYPES)))
    path.write_text("\n".join(output) + "\n", encoding="utf-8")
    path.chmod(0o600)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--workspace-id", required=True)
    args = parser.parse_args()
    update_env(args.env, args.workspace_id)


if __name__ == "__main__":
    main()
