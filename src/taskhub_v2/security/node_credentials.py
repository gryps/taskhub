from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock

from taskhub_v2.security.encryption import SecretCipher


class NodeCredentialError(RuntimeError):
    pass


class NodeCredentialVault:
    """Encrypted, Seed-local store for per-node bearer credentials."""

    def __init__(self, path: str, cipher: SecretCipher | None):
        self.path = Path(path)
        self.cipher = cipher
        self.lock = RLock()

    def issue(self, node_id: str) -> str:
        with self.lock:
            records = self._read()
            current = records.get(node_id)
            if current and not current.get("revoked_at"):
                raise NodeCredentialError(f"node credential already exists: {node_id}")
            return self._write_new(
                records, node_id, int((current or {}).get("version", 0)) + 1
            )

    def ensure(self, node_id: str) -> str:
        with self.lock:
            secret = self._resolve_unlocked(node_id)
            if secret:
                return secret
            records = self._read()
            current = records.get(node_id) or {}
            return self._write_new(records, node_id, int(current.get("version", 0)) + 1)

    def rotate(self, node_id: str) -> str:
        with self.lock:
            records = self._read()
            current = records.get(node_id)
            if not current or current.get("revoked_at"):
                raise NodeCredentialError(f"active node credential not found: {node_id}")
            return self._write_new(records, node_id, int(current["version"]) + 1)

    def revoke(self, node_id: str) -> None:
        with self.lock:
            records = self._read()
            current = records.get(node_id)
            if not current or current.get("revoked_at"):
                return
            current["revoked_at"] = _now()
            current["ciphertext"] = ""
            self._write(records)

    def resolve(self, node_id: str) -> str:
        with self.lock:
            return self._resolve_unlocked(node_id)

    def metadata(self, node_id: str) -> dict:
        with self.lock:
            record = self._read().get(node_id)
            if not record:
                return {"status": "missing", "version": 0}
            return {
                "status": "revoked" if record.get("revoked_at") else "active",
                "version": int(record.get("version", 0)),
                "fingerprint": record.get("fingerprint", ""),
                "created_at": record.get("created_at", ""),
                "rotated_at": record.get("rotated_at", ""),
                "revoked_at": record.get("revoked_at", ""),
            }

    def _resolve_unlocked(self, node_id: str) -> str:
        record = self._read().get(node_id)
        if not record or record.get("revoked_at") or not record.get("ciphertext"):
            return ""
        if not self.cipher:
            raise NodeCredentialError("尚未配置节点凭据加密根密钥")
        return self.cipher.decrypt(record["ciphertext"])

    def _write_new(self, records: dict, node_id: str, version: int) -> str:
        if not self.cipher:
            raise NodeCredentialError("尚未配置节点凭据加密根密钥")
        token = secrets.token_urlsafe(48)
        now = _now()
        created_at = (records.get(node_id) or {}).get("created_at") or now
        records[node_id] = {
            "version": version,
            "fingerprint": hashlib.sha256(token.encode()).hexdigest()[:16],
            "ciphertext": self.cipher.encrypt(token),
            "created_at": created_at,
            "rotated_at": now if version > 1 else "",
            "revoked_at": "",
        }
        self._write(records)
        return token

    def _read(self) -> dict:
        if not self.path.is_file():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return dict(payload.get("credentials", {}))
        except (OSError, ValueError, TypeError) as exc:
            raise NodeCredentialError("节点凭据库损坏或不可读") from exc

    def _write(self, records: dict) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"credentials": records}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)


def _now() -> str:
    return datetime.now(UTC).isoformat()
