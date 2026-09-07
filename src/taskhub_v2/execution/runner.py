import asyncio
import hashlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path

import httpx

from taskhub_v2.domain.models import (
    CodeChangeSummary,
    ModelResult,
    NodeDefinition,
    Plan,
    ScheduledCoding,
    ScheduledTests,
    TestExecution,
)
from taskhub_v2.providers.egress import direct_environment

EXCLUDED_PARTS = {".git", "__pycache__", ".pytest_cache", "node_modules", "dist", "build"}
EXCLUDED_NAMES = {".env", ".env.local", "auth.json", "credentials.json", ".coverage"}


class NodeExecutionError(RuntimeError):
    reason = "execution_node_unavailable"

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class NodeRunner:
    def __init__(self, token: str, transport=None, local_coder=None):
        self.token = token
        self.transport = transport
        self.local_coder = local_coder

    async def run(
        self,
        node: NodeDefinition,
        job_id: str,
        commands: list[list[str]],
        timeout: int,
        workdir: str,
        *,
        required_capabilities: set[str] | None = None,
        target_url: str = "",
        git_commit: str = "",
        artifact_paths: list[str] | None = None,
    ) -> ScheduledTests:
        if node.kind == "local":
            tests = await self._run_local(commands, timeout, workdir)
            return ScheduledTests(node_id=node.id, tests=tests)
        return await self._run_remote(
            node,
            job_id,
            commands,
            timeout,
            workdir,
            required_capabilities or set(),
            target_url,
            git_commit,
            artifact_paths or [],
        )

    async def health(self, node: NodeDefinition) -> dict:
        if node.kind == "local":
            import shutil

            from taskhub_v2.config import get_settings
            from taskhub_v2.services.diagnostics import controller_diagnostics

            return {
                "status": "ok",
                "node_id": node.id,
                "cpu_count": os.cpu_count() or 1,
                "capabilities": {
                    "git": bool(shutil.which("git")),
                    "python3": True,
                    "node": bool(shutil.which("node")),
                    "npm": bool(shutil.which("npm")),
                    "pytest": importlib.util.find_spec("pytest") is not None,
                    "coding": self.local_coder is not None,
                },
                "system": controller_diagnostics(get_settings()),
            }
        try:
            async with self._client(timeout=100 if "browser_acceptance" in node.workloads else 5) as client:
                response = await client.get(
                    f"{node.url.rstrip('/')}/api/health", headers=self._headers()
                )
                response.raise_for_status()
                return response.json()
        except Exception as exc:
            return {"status": "unavailable", "node_id": node.id, "detail": str(exc)[:200]}

    async def run_coding(
        self,
        node: NodeDefinition,
        job_id: str,
        requirement: str,
        plan: Plan,
        feedback: str,
        timeout: int,
        workdir: str,
    ) -> ScheduledCoding:
        if node.kind == "local":
            if self.local_coder is None:
                raise NodeExecutionError("local coding provider is unavailable")
            result = await self.local_coder.modify(requirement, plan, workdir, feedback)
            return ScheduledCoding(node_id=node.id, result=result)
        archive = await asyncio.to_thread(self._archive, Path(workdir))
        digest = hashlib.sha256(archive).hexdigest()
        try:
            async with self._client(timeout=max(timeout + 30, 120)) as client:
                await self._upload(client, node, job_id, archive, digest)
                response = await client.post(
                    f"{node.url.rstrip('/')}/api/jobs/{job_id}/code",
                    json={
                        "requirement": requirement,
                        "plan": plan.model_dump(),
                        "feedback": feedback,
                        "archive_sha256": digest,
                    },
                    headers=self._headers(),
                )
                response.raise_for_status()
            result = await asyncio.to_thread(
                self._apply_change_bundle, Path(workdir), response.content
            )
            return ScheduledCoding(node_id=node.id, result=result)
        except Exception as exc:
            raise NodeExecutionError(f"node {node.id} coding failed: {str(exc)[:300]}") from exc

    async def _run_remote(
        self,
        node: NodeDefinition,
        job_id: str,
        commands: list[list[str]],
        timeout: int,
        workdir: str,
        required_capabilities: set[str],
        target_url: str,
        git_commit: str,
        artifact_paths: list[str],
    ) -> ScheduledTests:
        archive = await asyncio.to_thread(self._archive, Path(workdir))
        digest = hashlib.sha256(archive).hexdigest()
        try:
            async with self._client(timeout=max(timeout + 30, 120)) as client:
                await self._upload(client, node, job_id, archive, digest)
                executed = await client.post(
                    f"{node.url.rstrip('/')}/api/jobs/{job_id}/execute",
                    json={
                        "commands": commands,
                        "timeout_seconds": timeout,
                        "archive_sha256": digest,
                        "required_capabilities": sorted(required_capabilities),
                        "target_url": target_url,
                        "git_commit": git_commit,
                        "artifact_paths": artifact_paths,
                    },
                    headers=self._headers(),
                )
                executed.raise_for_status()
        except Exception as exc:
            raise NodeExecutionError(f"node {node.id} failed: {str(exc)[:300]}") from exc
        payload = executed.json()
        artifacts = []
        for item in payload.get("artifacts", []):
            response = await self._download_artifact(node, job_id, item["path"])
            if (
                len(response) != item["size"]
                or hashlib.sha256(response).hexdigest() != item["sha256"]
            ):
                raise NodeExecutionError(f"node {node.id} returned an invalid artifact")
            artifacts.append({**item, "content": response})
        return ScheduledTests(
            node_id=node.id,
            tests=[TestExecution.model_validate(item) for item in payload["tests"]],
            metadata={**payload.get("metadata", {}), "downloaded_artifacts": artifacts},
        )

    async def _download_artifact(self, node, job_id: str, path: str) -> bytes:
        from urllib.parse import quote
        async with self._client(timeout=120) as client:
            response = await client.get(
                f"{node.url.rstrip('/')}/api/jobs/{job_id}/artifacts/{quote(path, safe='/')}",
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.content

    async def _upload(self, client, node, job_id, archive, digest) -> None:
        uploaded = await client.put(
            f"{node.url.rstrip('/')}/api/jobs/{job_id}/workspace",
            params={"sha256": digest},
            content=archive,
            headers={**self._headers(), "Content-Type": "application/gzip"},
        )
        uploaded.raise_for_status()

    def _client(self, timeout: int) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, trust_env=False, transport=self.transport)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    @staticmethod
    def _archive(workdir: Path) -> bytes:
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as bundle:
            for path in sorted(workdir.rglob("*")):
                relative = path.relative_to(workdir)
                if (
                    EXCLUDED_PARTS.intersection(relative.parts)
                    or path.name in EXCLUDED_NAMES
                    or path.name.startswith(".env.")
                ):
                    continue
                if path.is_symlink() or not path.is_file():
                    continue
                bundle.add(path, arcname=str(relative), recursive=False)
        return output.getvalue()

    @staticmethod
    def _apply_change_bundle(workdir: Path, payload: bytes) -> ModelResult[CodeChangeSummary]:
        with tempfile.TemporaryDirectory(prefix="taskhub-change-") as temporary:
            root = Path(temporary)
            with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as bundle:
                for member in bundle.getmembers():
                    path = Path(member.name)
                    if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk():
                        raise ValueError("unsafe coding result")
                bundle.extractall(root, filter="data")
            manifest = json.loads((root / "manifest.json").read_text())
            changed = _validated_paths(manifest.get("changed_files", []))
            deleted = _validated_paths(manifest.get("deleted_files", []))
            for name in changed:
                source = root / "files" / name
                if not source.is_file():
                    raise ValueError("coding result is incomplete")
                target = workdir / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            for name in deleted:
                target = workdir / name
                if target.is_file():
                    target.unlink()
            return ModelResult(
                content=CodeChangeSummary(
                    summary=manifest["summary"], tests=manifest.get("tests", [])
                ),
                provider=manifest["provider"],
                model=manifest["model"],
                duration_ms=manifest.get("duration_ms", 0),
                failed_providers=manifest.get("failed_providers", []),
            )

    @staticmethod
    async def _run_local(
        commands: list[list[str]], timeout: int, workdir: str
    ) -> list[TestExecution]:
        results = []
        for command in commands:
            if not command:
                continue
            command = list(command)
            if Path(command[0]).name in {"python", "python3"}:
                command[0] = sys.executable
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=workdir,
                env=direct_environment(dict(os.environ)),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
            except TimeoutError:
                process.kill()
                await process.wait()
                results.append(
                    TestExecution(command=command, exit_code=124, output_tail="command timed out")
                )
                break
            results.append(
                TestExecution(
                    command=command,
                    exit_code=process.returncode,
                    output_tail=stdout.decode(errors="replace")[-4000:],
                )
            )
            if process.returncode:
                break
        return results


def _validated_paths(values: list[str]) -> list[Path]:
    result = []
    for value in values:
        path = Path(value)
        if (
            path.is_absolute()
            or ".." in path.parts
            or path.name in EXCLUDED_NAMES
            or path.name.startswith(".env.")
            or EXCLUDED_PARTS.intersection(path.parts)
        ):
            raise ValueError("unsafe coding result path")
        result.append(path)
    return result
