import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from taskhub_v2.node_agent.coding import snapshot


def workspace_fingerprint(target: Path) -> str:
    files = {
        name: digest
        for name, digest in snapshot(target).items()
        if not name.startswith(".taskhub-")
    }
    payload = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


@dataclass
class CodingResultCache:
    root: Path
    job_id: str
    semantic_request: dict

    @property
    def cache_root(self) -> Path:
        return self.root / ".taskhub-coding-results"

    @property
    def metadata_path(self) -> Path:
        return self.cache_root / f"{self.job_id}.json"

    @property
    def bundle_path(self) -> Path:
        return self.cache_root / f"{self.job_id}.tar.gz"

    @property
    def request_key(self) -> str:
        encoded = json.dumps(
            self.semantic_request, sort_keys=True, separators=(",", ":")
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    def read(self, target: Path) -> bytes | None:
        if not self.metadata_path.is_file() or not self.bundle_path.is_file():
            return None
        try:
            cached = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        reusable_fingerprints = {
            cached.get("input_fingerprint"),
            cached.get("output_fingerprint"),
        }
        if (
            cached.get("request_key") != self.request_key
            or workspace_fingerprint(target) not in reusable_fingerprints
        ):
            return None
        bundle = self.bundle_path.read_bytes()
        if cached.get("bundle_sha256") != hashlib.sha256(bundle).hexdigest():
            return None
        return bundle

    def write(self, target: Path, bundle: bytes, input_fingerprint: str) -> None:
        self.cache_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        bundle_temporary = self.bundle_path.with_suffix(".tmp")
        bundle_temporary.write_bytes(bundle)
        bundle_temporary.replace(self.bundle_path)
        metadata_temporary = self.metadata_path.with_suffix(".tmp")
        metadata_temporary.write_text(
            json.dumps(
                {
                    "request_key": self.request_key,
                    "input_fingerprint": input_fingerprint,
                    "output_fingerprint": workspace_fingerprint(target),
                    "bundle_sha256": hashlib.sha256(bundle).hexdigest(),
                }
            ),
            encoding="utf-8",
        )
        metadata_temporary.replace(self.metadata_path)
