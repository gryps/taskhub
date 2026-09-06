import hashlib
import os
import re
import tempfile
from pathlib import Path

from taskhub_v2.domain.models import Artifact


class ArtifactStore:
    def __init__(self, root: str):
        self.root = Path(root).resolve()

    def write_text(self, run_id: str, name: str, kind: str, content: str) -> Artifact:
        safe_run = re.sub(r"[^a-zA-Z0-9-]", "-", run_id)
        safe_name = re.sub(r"[^a-zA-Z0-9._-]", "-", name)
        directory = (self.root / safe_run).resolve()
        if not directory.is_relative_to(self.root):
            raise ValueError("artifact path escaped configured root")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = content.encode()
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
            sha256=hashlib.sha256(payload).hexdigest(),
            metadata={"bytes": len(payload)},
        )
