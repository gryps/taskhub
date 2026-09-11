import json
import re
from hashlib import sha256
from urllib.parse import urlsplit

SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{30,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
)


def validate_import_manifest(manifest: dict) -> None:
    source_uri = str(manifest.get("source_uri", ""))
    parsed = urlsplit(source_uri)
    if parsed.username or parsed.password:
        raise ValueError("capability source URI cannot contain credentials")
    encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
    if len(encoded.encode()) > 1_000_000:
        raise ValueError("capability pack manifest exceeds the 1 MB limit")
    if any(pattern.search(encoded) for pattern in SECRET_PATTERNS):
        raise ValueError("capability pack manifests cannot contain credentials")


def manifest_digest(pack) -> str:
    excluded = {
        "project_id",
        "status",
        "trusted_by",
        "trusted_at",
        "disabled_reason",
        "manifest_digest",
        "created_by",
        "created_at",
        "updated_at",
        "source_ids",
        "content_digest",
    }
    payload = pack.model_dump(mode="json", exclude=excluded)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


def semantic_version(version: str) -> tuple[int, ...]:
    return tuple(int(item) for item in version.split("-", 1)[0].split("."))
