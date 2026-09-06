import asyncio
import os
import re
import signal
import subprocess
import tempfile
import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
from psycopg.conninfo import make_conninfo

from taskhub_v2.browser.contract import PreviewContract


@dataclass
class PreviewInstance:
    run_id: str
    commit: str
    port: int
    schema: str
    url: str
    process: asyncio.subprocess.Process
    state_dir: str


class PreviewManager:
    """Owns preview ports/processes and always tears down their database schema."""

    def __init__(self, postgres_dsn: str, host: str = "192.168.31.31", ports=range(8400, 8500)):
        self.postgres_dsn = postgres_dsn
        self.host = host
        self.ports = tuple(ports)
        self._instances: dict[str, PreviewInstance] = {}
        self._lock = asyncio.Lock()

    async def start(self, run_id: str, worktree: str, commit: str, contract: PreviewContract) -> PreviewInstance:
        if not re.fullmatch(r"[a-fA-F0-9]{7,64}", commit):
            raise ValueError("preview requires an exact Git commit SHA")
        actual = await asyncio.to_thread(subprocess.run,
            ["git", "-C", worktree, "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, timeout=15)
        dirty = await asyncio.to_thread(subprocess.run,
            ["git", "-C", worktree, "status", "--porcelain", "--untracked-files=normal"],
            capture_output=True, text=True, check=True, timeout=15)
        if actual.stdout.strip() != commit or dirty.stdout.strip():
            raise ValueError("preview requires a clean workspace at the exact commit")
        async with self._lock:
            if run_id in self._instances:
                raise ValueError("preview run already exists")
            used = {item.port for item in self._instances.values()}
            port = next((candidate for candidate in self.ports if candidate not in used), None)
            if port is None:
                raise RuntimeError("no preview port is available")
            schema = f"taskhub_acceptance_{uuid4().hex}"
            await asyncio.to_thread(self._create_schema, schema)
            state_dir = tempfile.mkdtemp(prefix="taskhub-preview-")
            try:
                environment = {**os.environ, "PORT": str(port), "TASKHUB_PREVIEW_URL": f"http://{self.host}:{port}", "PGOPTIONS": f"-c search_path={schema}", "TASKHUB_GIT_COMMIT": commit, "TASKHUB_PREVIEW_STATE_DIR": state_dir, "TASKHUB_PREVIEW_DSN": make_conninfo(self.postgres_dsn, options=f"-csearch_path={schema}")}
                command = [
                    part.format(port=port, schema=schema, commit=commit)
                    for part in contract.command
                ]
                process = await asyncio.create_subprocess_exec(*command, cwd=Path(worktree), env=environment, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.STDOUT, start_new_session=True)
            except BaseException:
                await asyncio.to_thread(self._drop_schema, schema)
                shutil.rmtree(state_dir, ignore_errors=True)
                raise
            instance = PreviewInstance(run_id, commit, port, schema, f"http://{self.host}:{port}", process, state_dir)
            self._instances[run_id] = instance
        try:
            await self._wait_ready(instance.url + contract.health_path, contract.timeout_seconds, commit)
            return instance
        except BaseException:
            await self.stop(run_id)
            raise

    async def stop(self, run_id: str) -> None:
        async with self._lock:
            instance = self._instances.pop(run_id, None)
        if not instance:
            return
        try:
            if instance.process.returncode is None:
                os.killpg(instance.process.pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(instance.process.wait(), 10)
                except TimeoutError:
                    os.killpg(instance.process.pid, signal.SIGKILL)
                    await instance.process.wait()
        finally:
            try:
                await asyncio.to_thread(self._drop_schema, instance.schema)
            finally:
                shutil.rmtree(instance.state_dir, ignore_errors=True)

    async def _wait_ready(self, url: str, timeout: int, commit: str) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        async with httpx.AsyncClient(trust_env=False) as client:
            while asyncio.get_running_loop().time() < deadline:
                try:
                    response = await client.get(url, timeout=2)
                    if response.is_success:
                        if response.json().get("git_commit") != commit:
                            raise ValueError("preview health commit mismatch")
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.25)
        raise TimeoutError("preview health check timed out")

    def _create_schema(self, schema: str) -> None:
        with psycopg.connect(self.postgres_dsn) as connection:
            connection.execute(f'CREATE SCHEMA "{schema}"')

    def _drop_schema(self, schema: str) -> None:
        with psycopg.connect(self.postgres_dsn) as connection:
            connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
