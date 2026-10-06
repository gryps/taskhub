import hashlib
import hmac
import secrets
import time
from pathlib import Path
from threading import RLock
from typing import Any

from taskhub_v2.security.atomic_json import locked_file, write_json
from taskhub_v2.security.encryption import SecretCipher, SecretEncryptionError


class AgentAccessError(ValueError):
    pass


def bearer_token(header: str | None) -> str:
    if not header:
        return ""
    scheme, separator, value = header.partition(" ")
    return value.strip() if separator and scheme.lower() == "bearer" else ""


class AgentAccessService:
    """Persist revocable agent credentials without storing bearer secrets."""

    def __init__(
        self,
        state_file: str,
        cipher: SecretCipher | None,
        *,
        pairing_ttl_seconds: int = 600,
        credential_days: int = 30,
    ):
        self.state_file = Path(state_file) if state_file else None
        self.cipher = cipher
        self.pairing_ttl_seconds = max(120, pairing_ttl_seconds)
        self.credential_days = min(90, max(1, credential_days))
        self._lock = RLock()

    def configured(self) -> bool:
        return self.state_file is not None and self.cipher is not None

    def begin_pairing(self, label: str, identity: str) -> dict[str, Any]:
        self._require_configuration()
        label = " ".join(label.split()).strip()
        if not 2 <= len(label) <= 80:
            raise AgentAccessError("connection label must contain 2 to 80 characters")
        now = int(time.time())
        pairing_id = secrets.token_urlsafe(18)
        device_secret = secrets.token_urlsafe(32)
        user_code = self._user_code()
        with self._lock, locked_file(self.state_file):
            state = self._pruned_state(now)
            pending = [
                item
                for item in state["pairings"].values()
                if item.get("identity") == identity and item.get("status") == "pending"
            ]
            if len(pending) >= 5:
                raise AgentAccessError("too many pending pairings from this client")
            state["pairings"][pairing_id] = {
                "pairing_id": pairing_id,
                "label": label,
                "user_code": user_code,
                "device_hash": self._digest(device_secret),
                "identity": identity,
                "status": "pending",
                "created_at": now,
                "expires_at": now + self.pairing_ttl_seconds,
            }
            self._write_state(state)
        return {
            "pairing_id": pairing_id,
            "device_secret": device_secret,
            "user_code": user_code,
            "expires_at": now + self.pairing_ttl_seconds,
            "poll_interval_seconds": 2,
        }

    def pending_pairings(self) -> list[dict[str, Any]]:
        self._require_configuration()
        state = self._read_state()
        now = int(time.time())
        return [
            self._public_pairing(item)
            for item in state["pairings"].values()
            if item.get("status") == "pending" and int(item.get("expires_at", 0)) > now
        ]

    def approve_pairing(
        self,
        pairing_id: str,
        *,
        owner: str,
        role: str,
        expires_days: int | None = None,
    ) -> dict[str, Any]:
        self._require_configuration()
        now = int(time.time())
        days = self.credential_days if expires_days is None else min(90, max(1, expires_days))
        with self._lock, locked_file(self.state_file):
            state = self._pruned_state(now)
            pairing = state["pairings"].get(pairing_id)
            if not pairing or pairing.get("status") != "pending":
                raise AgentAccessError("pairing is missing, expired, or already handled")
            credential_id = secrets.token_urlsafe(12)
            token = f"thv2.{credential_id}.{secrets.token_urlsafe(32)}"
            credential = {
                "credential_id": credential_id,
                "label": pairing["label"],
                "owner": owner,
                "actor": f"agent:{pairing['label']}",
                "role": role,
                "token_hash": self._digest(token),
                "created_at": now,
                "expires_at": now + days * 86400,
                "last_used_at": None,
                "revoked_at": None,
            }
            state["credentials"][credential_id] = credential
            pairing.update(
                {
                    "status": "approved",
                    "approved_at": now,
                    "approved_by": owner,
                    "credential_id": credential_id,
                    "grant": self.cipher.encrypt(token),
                }
            )
            self._write_state(state)
        return self._public_pairing(pairing)

    def reject_pairing(self, pairing_id: str, actor: str) -> None:
        self._require_configuration()
        with self._lock, locked_file(self.state_file):
            state = self._pruned_state(int(time.time()))
            pairing = state["pairings"].get(pairing_id)
            if not pairing or pairing.get("status") != "pending":
                raise AgentAccessError("pairing is missing, expired, or already handled")
            pairing.update(
                {
                    "status": "rejected",
                    "rejected_at": int(time.time()),
                    "rejected_by": actor,
                }
            )
            self._write_state(state)

    def exchange_pairing(self, pairing_id: str, device_secret: str) -> dict[str, Any]:
        self._require_configuration()
        now = int(time.time())
        with self._lock, locked_file(self.state_file):
            state = self._pruned_state(now)
            pairing = state["pairings"].get(pairing_id)
            if not pairing or not hmac.compare_digest(
                str(pairing.get("device_hash", "")), self._digest(device_secret)
            ):
                raise AgentAccessError("invalid pairing credentials")
            if pairing.get("status") == "pending":
                return {"status": "pending", "expires_at": pairing["expires_at"]}
            if pairing.get("status") == "rejected":
                return {"status": "rejected"}
            grant = str(pairing.get("grant") or "")
            if pairing.get("status") != "approved" or not grant:
                raise AgentAccessError("pairing grant was already consumed")
            try:
                token = self.cipher.decrypt(grant)
            except SecretEncryptionError as error:
                raise AgentAccessError("pairing grant cannot be decrypted") from error
            credential_id = str(pairing["credential_id"])
            del state["pairings"][pairing_id]
            self._write_state(state)
        return {"status": "approved", "token": token, "credential_id": credential_id}

    def authenticate(self, token: str) -> dict[str, Any] | None:
        if not self.configured() or not token.startswith("thv2."):
            return None
        parts = token.split(".", 2)
        if len(parts) != 3:
            return None
        credential_id = parts[1]
        now = int(time.time())
        with self._lock, locked_file(self.state_file):
            state = self._pruned_state(now)
            credential = state["credentials"].get(credential_id)
            if (
                not credential
                or credential.get("revoked_at")
                or int(credential.get("expires_at", 0)) <= now
                or not hmac.compare_digest(
                    str(credential.get("token_hash", "")), self._digest(token)
                )
            ):
                return None
            last_used = int(credential.get("last_used_at") or 0)
            if now - last_used >= 60:
                credential["last_used_at"] = now
                self._write_state(state)
        return {
            "auth_type": "bearer",
            "actor": credential["actor"],
            "owner": credential["owner"],
            "role": credential["role"],
            "credential_id": credential_id,
            "expires": credential["expires_at"],
        }

    def list_credentials(self) -> list[dict[str, Any]]:
        self._require_configuration()
        state = self._read_state()
        return [self._public_credential(item) for item in state["credentials"].values()]

    def revoke_credential(self, credential_id: str, actor: str) -> None:
        self._require_configuration()
        with self._lock, locked_file(self.state_file):
            state = self._pruned_state(int(time.time()))
            credential = state["credentials"].get(credential_id)
            if not credential:
                raise AgentAccessError("agent credential not found")
            credential.update({"revoked_at": int(time.time()), "revoked_by": actor})
            self._write_state(state)

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _user_code() -> str:
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        value = "".join(secrets.choice(alphabet) for _ in range(8))
        return f"{value[:4]}-{value[4:]}"

    def _require_configuration(self) -> None:
        if not self.configured():
            raise AgentAccessError("agent access is not configured")

    def _read_state(self) -> dict[str, Any]:
        if not self.state_file or not self.state_file.is_file():
            return {"version": 1, "pairings": {}, "credentials": {}}
        try:
            import json

            value = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"version": 1, "pairings": {}, "credentials": {}}
        return {
            "version": 1,
            "pairings": value.get("pairings", {}),
            "credentials": value.get("credentials", {}),
        }

    def _pruned_state(self, now: int) -> dict[str, Any]:
        state = self._read_state()
        state["pairings"] = {
            key: item
            for key, item in state["pairings"].items()
            if int(item.get("expires_at", 0)) > now
        }
        return state

    def _write_state(self, state: dict[str, Any]) -> None:
        if self.state_file:
            write_json(self.state_file, state)

    @staticmethod
    def _public_pairing(item: dict[str, Any]) -> dict[str, Any]:
        fields = (
            "pairing_id",
            "label",
            "user_code",
            "status",
            "created_at",
            "expires_at",
            "approved_at",
            "approved_by",
            "rejected_at",
            "rejected_by",
            "credential_id",
        )
        return {key: item.get(key) for key in fields if key in item}

    @staticmethod
    def _public_credential(item: dict[str, Any]) -> dict[str, Any]:
        fields = (
            "credential_id",
            "label",
            "owner",
            "actor",
            "role",
            "created_at",
            "expires_at",
            "last_used_at",
            "revoked_at",
            "revoked_by",
        )
        return {key: item.get(key) for key in fields if key in item}
