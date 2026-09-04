from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from uuid import UUID

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
        browser=BrowserConfig(channel=BrowserChannel(request.get("browser_channel", "msedge")), executable_path=None),
        report=ReportConfig(artifact_dir=Path(request["artifact_dir"])),
        probe=ProbeConfig(),
    )
    client = ControlPlaneClient(
        api_base_url=request["api_base_url"],
        agent_id=request["agent_id"],
        device_secret=secret,
    )
    claimed = client.claim()
    if claimed is None:
        print(json.dumps({"status": "idle"}))
        return 3
    expected = UUID(str(request["listing_task_id"]))
    if claimed.id != expected:
        raise RuntimeError("claimed listing task does not match TaskHub target")
    client.claim = lambda: claimed  # type: ignore[method-assign]
    outcome = run_task_once(client, ListingBrowserDriver(profile=validated.profile, browser=validated.browser))
    if outcome is None:
        raise RuntimeError("listing task disappeared after claim")
    print(json.dumps({
        "status": outcome.value,
        "listing_task_id": str(claimed.id),
        "artifacts": [str(validated.report.artifact_dir)],
    }))
    return 0 if outcome.value in {"draft_saved", "waiting_category"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
