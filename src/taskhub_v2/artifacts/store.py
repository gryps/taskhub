import hashlib
import hmac
import os
import re
import tempfile
from pathlib import Path

from taskhub_v2.domain.models import Artifact


class ArtifactStore:
    def __init__(self, root: str):
        self.root = Path(root).resolve()

    def write_text(self, run_id: str, name: str, kind: str, content: str) -> Artifact:
        return self.write_bytes(run_id, name, kind, content.encode())

    def write_bytes(
        self,
        run_id: str,
        name: str,
        kind: str,
        payload: bytes,
        *,
        expected_sha256: str | None = None,
        max_bytes: int = 100 * 1024 * 1024,
        metadata: dict | None = None,
    ) -> Artifact:
        """Persist an agent artifact only after size and digest verification."""
        if len(payload) > max_bytes:
            raise ValueError("artifact exceeds configured size limit")
        digest = hashlib.sha256(payload).hexdigest()
        if expected_sha256 is not None and not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
            raise ValueError("invalid artifact digest")
        if expected_sha256 is not None and not hmac.compare_digest(digest, expected_sha256):
            raise ValueError("artifact digest mismatch")
        safe_run = re.sub(r"[^a-zA-Z0-9-]", "-", run_id)
        safe_name = re.sub(r"[^a-zA-Z0-9._-]", "-", name)
        if safe_name in {"", ".", ".."}:
            raise ValueError("invalid artifact name")
        directory = (self.root / safe_run).resolve()
        if not directory.is_relative_to(self.root):
            raise ValueError("artifact path escaped configured root")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{safe_name}.", dir=directory)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
            target = directory / safe_name
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return Artifact(
            kind=kind,
            uri=f"artifact://{safe_run}/{safe_name}",
            sha256=digest,
            metadata={"bytes": len(payload), "verified": True, **(metadata or {})},
        )
