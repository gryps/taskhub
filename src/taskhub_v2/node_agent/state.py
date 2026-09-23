from __future__ import annotations

import asyncio
import hmac
from collections import deque
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from re import Pattern

from fastapi import HTTPException

from taskhub_v2.node_agent.system_load import RollingLoadSampler


class NodeRuntime:
    def __init__(
        self, node_id: str, role: str, token: str, root: str, max_upload_bytes: int,
        workloads: dict[str, list[str]], job_pattern: Pattern[str], slots: int = 1,
    ):
        self.node_id = node_id
        self.role = role if role in workloads else "test"
        self.token = token
        self.root = Path(root).resolve()
        self.max_upload_bytes = max_upload_bytes
        self.job_pattern = job_pattern
        self.locks: dict[str, asyncio.Lock] = {}
        self.slots = max(1, slots)
        self.active_workloads = 0
        self._workload_slots = asyncio.Semaphore(self.slots)
        self.load_sampler = RollingLoadSampler()
        self.events: deque[dict] = deque(maxlen=200)

    def record(self, event: str, *, job_id: str = "", result: str = "passed") -> None:
        self.events.append(
            {"created_at": datetime.now(UTC).isoformat(), "event": event,
             "job_id": job_id, "result": result}
        )

    def authorize(self, authorization: str) -> None:
        supplied = authorization.removeprefix("Bearer ")
        if not self.token or not hmac.compare_digest(supplied, self.token):
            raise HTTPException(status_code=401, detail="invalid node token")

    def job_dir(self, job_id: str) -> Path:
        if not self.job_pattern.fullmatch(job_id):
            raise HTTPException(status_code=422, detail="invalid job ID")
        target = (self.root / job_id).resolve()
        if not target.is_relative_to(self.root):
            raise HTTPException(status_code=422, detail="invalid job path")
        return target

    @asynccontextmanager
    async def workload_slot(self):
        """Enforce node capacity even when the controller restarts mid-job."""
        async with self._workload_slots:
            self.active_workloads += 1
            try:
                yield
            finally:
                self.active_workloads -= 1

    @staticmethod
    def result_file(target: Path) -> Path:
        return target / ".taskhub-execution-result.json"
