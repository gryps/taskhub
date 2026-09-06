import asyncio
import os
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg

from taskhub_v2.browser.contract import PreviewContract


@dataclass
class PreviewInstance:
    run_id: str
    commit: str
    port: int
    schema: str
    url: str
    process: asyncio.subprocess.Process


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
        async with self._lock:
            used = {item.port for item in self._instances.values()}
            port = next((candidate for candidate in self.ports if candidate not in used), None)
            if port is None:
                raise RuntimeError("no preview port is available")
            schema = f"taskhub_acceptance_{uuid4().hex}"
            await asyncio.to_thread(self._create_schema, schema)
            environment = {**os.environ, "PORT": str(port), "TASKHUB_PREVIEW_URL": f"http://{self.host}:{port}", "PGOPTIONS": f"-c search_path={schema}", "TASKHUB_GIT_COMMIT": commit}
            command = [
                part.format(port=port, schema=schema, commit=commit)
                for part in contract.command
            ]
            try:
                process = await asyncio.create_subprocess_exec(*command, cwd=Path(worktree), env=environment, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            except Exception:
                await asyncio.to_thread(self._drop_schema, schema)
                raise
            instance = PreviewInstance(run_id, commit, port, schema, f"http://{self.host}:{port}", process)
            self._instances[run_id] = instance
        try:
            await self._wait_ready(instance.url + contract.health_path, contract.timeout_seconds)
            return instance
        except Exception:
            await self.stop(run_id)
            raise

    async def stop(self, run_id: str) -> None:
        async with self._lock:
            instance = self._instances.pop(run_id, None)
        if not instance:
            return
        if instance.process.returncode is None:
            instance.process.terminate()
            try:
                await asyncio.wait_for(instance.process.wait(), 10)
            except TimeoutError:
                instance.process.kill()
                await instance.process.wait()
        await asyncio.to_thread(self._drop_schema, instance.schema)

    async def _wait_ready(self, url: str, timeout: int) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        async with httpx.AsyncClient(trust_env=False) as client:
            while asyncio.get_running_loop().time() < deadline:
                try:
                    if (await client.get(url, timeout=2)).is_success:
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
