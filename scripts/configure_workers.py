from __future__ import annotations

from pathlib import Path


ENV_PATH = Path("/home/gryps/apps/langgraph-control/.env")
REQUIRED_WORKERS = [
    "http://192.168.31.24:8124",
    "http://192.168.31.34:8125",
    "http://192.168.31.31:8126",
    "http://192.168.31.31:8127",
]
REQUIRED_NO_PROXY = ["127.0.0.1", "localhost", "192.168.31.24", "192.168.31.31", "192.168.31.34"]


def merge_csv(current: str, required: list[str]) -> str:
    values = [item.strip() for item in current.split(",") if item.strip()]
    return ",".join(dict.fromkeys([*values, *required]))


def main() -> None:
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    output: list[str] = []
    found = False
    cooldown_found = False
    no_proxy_found = {"NO_PROXY": False, "no_proxy": False}
    for line in lines:
        if line.startswith("WORKER_URLS="):
            current = [item.strip().rstrip("/") for item in line.partition("=")[2].split(",") if item.strip()]
            merged = list(dict.fromkeys([*current, *REQUIRED_WORKERS]))
            output.append("WORKER_URLS=" + ",".join(merged))
            found = True
        elif line.startswith("PROVIDER_COOLDOWN_SECONDS="):
            output.append("PROVIDER_COOLDOWN_SECONDS=60")
            cooldown_found = True
        elif line.startswith("NO_PROXY=") or line.startswith("no_proxy="):
            key, _, current = line.partition("=")
            output.append(f"{key}={merge_csv(current, REQUIRED_NO_PROXY)}")
            no_proxy_found[key] = True
        else:
            output.append(line)
    if not found:
        output.append("WORKER_URLS=" + ",".join(REQUIRED_WORKERS))
    if not cooldown_found:
        output.append("PROVIDER_COOLDOWN_SECONDS=60")
    for key, present in no_proxy_found.items():
        if not present:
            output.append(f"{key}={','.join(REQUIRED_NO_PROXY)}")
    ENV_PATH.write_text("\n".join(output) + "\n", encoding="utf-8")
    ENV_PATH.chmod(0o600)
    print("worker URLs configured")


if __name__ == "__main__":
    main()
