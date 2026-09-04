#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish a worker artifact to TaskHub")
    parser.add_argument("file", type=Path)
    parser.add_argument("--project", required=True)
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--task-id")
    args = parser.parse_args()
    controller = os.environ["TASKHUB_CONTROLLER_URL"].rstrip("/")
    token = os.environ["TASKHUB_WORKER_TOKEN"]
    path = args.file.resolve(strict=True)
    artifact_id = uuid.uuid4()
    query = urllib.parse.urlencode({
        "project": args.project, "name": path.name, "source_path": str(path),
        **({"task_id": args.task_id} if args.task_id else {}),
    })
    request = urllib.request.Request(
        f"{controller}/taskhub/integration/artifacts/{artifact_id}/content?{query}",
        data=path.read_bytes(), method="PUT",
        headers={
            "content-type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            "x-taskhub-worker-token": token, "x-worker-id": args.worker_id,
        },
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=120) as response:
        print(json.dumps(json.load(response), ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
