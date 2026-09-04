from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from douyin_executor.api_client import ControlPlaneClient
from douyin_executor.config import BrowserChannel, BrowserConfig, ExecutorConfig, ProbeConfig, ReportConfig
from douyin_executor.listing_driver import ListingBrowserDriver
from douyin_executor.task_runner import run_task_once


def main() -> int:
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    secret = os.environ.get("DOUYIN_EXECUTOR_DEVICE_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError("listing executor device secret is not configured")
    validated = ExecutorConfig(
        profile=Path(request["profile"]),
        browser=BrowserConfig(channel=BrowserChannel(request.get("browser_channel", "edge")), executable_path=None),
        report=ReportConfig(artifact_dir=Path(request["artifact_dir"])),
        probe=ProbeConfig(),
    )
    client = ControlPlaneClient(
        api_base_url=request["api_base_url"],
        agent_id=request["agent_id"],
        device_secret=secret,
    )
    outcome = run_task_once(client, ListingBrowserDriver(profile=validated.profile, browser=validated.browser))
    if outcome is None:
        print(json.dumps({"status": "idle"}))
        return 3
    print(json.dumps({"status": outcome.value, "artifacts": [str(validated.report.artifact_dir)]}))
    return 0 if outcome.value in {"draft_saved", "waiting_category"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
