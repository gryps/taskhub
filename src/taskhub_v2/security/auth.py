import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from threading import RLock
from typing import Any

from taskhub_v2.security.passwords import password_record, verify_password
from taskhub_v2.security.rbac import ROLE_PERMISSIONS

SESSION_COOKIE = "taskhub_v2_session"
CSRF_COOKIE = "taskhub_v2_csrf"


class AuthConfigurationError(RuntimeError):
    pass


class PasswordSetupError(RuntimeError):
    pass


class UserManagementError(ValueError):
    pass


class AuthService:
    def __init__(
        self,
        admin_token: str,
        session_secret: str,
        password_file: str = "",
        *,
        users_file: str = "",
        session_state_file: str = "",
        signing_keys_file: str = "",
        session_idle_seconds: int = 1800,
        session_absolute_seconds: int = 43200,
        login_max_failures: int = 5,
        login_window_seconds: int = 900,
        login_lock_seconds: int = 900,
    ):
        self.admin_token = admin_token
        self.session_secret = session_secret
        self.password_file = Path(password_file) if password_file else None
        self.users_file = Path(users_file) if users_file else None
        self.session_state_file = Path(session_state_file) if session_state_file else None
        self.signing_keys_file = Path(signing_keys_file) if signing_keys_file else None
        self.session_idle_seconds = max(60, session_idle_seconds)
        self.session_absolute_seconds = max(self.session_idle_seconds, session_absolute_seconds)
        self.login_max_failures = max(2, login_max_failures)
        self.login_window_seconds = max(60, login_window_seconds)
        self.login_lock_seconds = max(60, login_lock_seconds)
        self._lock = RLock()
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    def configured(self) -> bool:
        return bool(self.admin_token and self.session_secret)

    def valid_admin_token(self, token: str) -> bool:
        self._require_configuration()
        return bool(token) and hmac.compare_digest(token, self.admin_token)

    def password_login_enabled(self) -> bool:
        return self.password_file is not None

    def setup_required(self) -> bool:
        return bool(self.password_file and not self.password_file.is_file())

    def login_locked_seconds(self, identity: str) -> int:
        remaining = self._locked_until.get(identity, 0) - time.time()
        return max(0, int(remaining + 0.999))

    def authenticate(self, username: str, secret: str, identity: str) -> dict[str, Any] | None:
        self._require_configuration()
        key = f"{identity}:{username.lower()}"
        if self.login_locked_seconds(key):
            raise PermissionError("too many login failures")
        user = self._find_user(username)
        valid = False
        if user and user.get("enabled", True):
            valid = verify_password(secret, user)
        elif username == "admin":
            valid = self.valid_login_secret(secret)
            user = {"username": "admin", "role": "administrator", "enabled": True}
        if valid:
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)
            return user
        now = time.time()
        failures = [
            item for item in self._failures.get(key, []) if now - item < self.login_window_seconds
        ]
        failures.append(now)
        self._failures[key] = failures
        if len(failures) >= self.login_max_failures:
            self._locked_until[key] = now + self.login_lock_seconds
        return None

    def valid_login_secret(self, secret: str) -> bool:
        self._require_configuration()
        if not self.password_file:
            return self.valid_admin_token(secret)
        record = self._read_password_record()
        return bool(record and secret and verify_password(secret, record))

    def initialize_password(self, bootstrap_token: str, password: str) -> None:
        self._require_configuration()
        if not self.password_file:
            raise PasswordSetupError("password login is not enabled")
        if self.password_file.exists():
            raise PasswordSetupError("administrator password is already configured")
        if not self.valid_admin_token(bootstrap_token):
            raise PermissionError("invalid bootstrap token")
        record = password_record(password)
        self.password_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.password_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as error:
            raise PasswordSetupError("administrator password is already configured") from error
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as target:
                json.dump(record, target, separators=(",", ":"))
                target.write("\n")
                target.flush()
                os.fsync(target.fileno())
        except Exception:
            self.password_file.unlink(missing_ok=True)
            raise

    def create_session(
        self, username: str = "admin", role: str = "administrator"
    ) -> tuple[str, str]:
        self._require_configuration()
        now = int(time.time())
        csrf = secrets.token_urlsafe(24)
        session_id = secrets.token_urlsafe(24)
        payload = {
            "session_id": session_id,
            "actor": username,
            "role": role,
            "csrf": csrf,
            "issued": now,
            "expires": now + self.session_absolute_seconds,
        }
        encoded = self._encode(json.dumps(payload, separators=(",", ":")).encode())
        signature = self._sign(encoded)
        self._update_session(session_id, now, revoked=False, actor=username)
        return f"{encoded}.{signature}", csrf

    def read_session(self, value: str | None, *, touch: bool = True) -> dict[str, Any] | None:
        if not self.configured() or not value or "." not in value:
            return None
        encoded, signature = value.rsplit(".", 1)
        if not any(
            hmac.compare_digest(signature, self._signature(key, encoded)) for key in self._keys()
        ):
            return None
        try:
            payload = json.loads(self._decode(encoded))
        except (ValueError, json.JSONDecodeError):
            return None
        now = int(time.time())
        if int(payload.get("expires", 0)) <= now:
            return None
        session_id = str(payload.get("session_id") or "")
        if not session_id:  # Backward-compatible stateless session during upgrade.
            return payload
        if not self.session_state_file:
            return payload
        state = self._sessions().get(session_id)
        if (
            not state
            or state.get("revoked")
            or now - int(state.get("last_seen", 0)) > self.session_idle_seconds
        ):
            return None
        if touch and now - int(state.get("last_seen", 0)) >= 30:
            self._update_session(
                session_id, now, revoked=False, actor=str(payload.get("actor", ""))
            )
        payload["idle_expires"] = int(state.get("last_seen", now)) + self.session_idle_seconds
        return payload

    def revoke_session(self, session: dict[str, Any] | None) -> None:
        session_id = str((session or {}).get("session_id") or "")
        if session_id:
            self._update_session(session_id, int(time.time()), revoked=True)

    def rotate_signing_key(self) -> dict[str, Any]:
        if not self.signing_keys_file:
            raise AuthConfigurationError("session signing key file is not configured")
        current = self._keys()[0]
        new_key = secrets.token_urlsafe(48)
        self._write_json(
            self.signing_keys_file, {"keys": [new_key, current], "rotated_at": int(time.time())}
        )
        sessions = self._sessions()
        for item in sessions.values():
            item["revoked"] = True
        self._write_sessions(sessions)
        return {"rotated": True, "sessions_revoked": len(sessions)}

    def list_users(self) -> list[dict[str, Any]]:
        users = [{"username": "admin", "role": "administrator", "enabled": True, "built_in": True}]
        users.extend(
            {key: value for key, value in item.items() if key != "password"}
            for item in self._user_records()
        )
        return users

    def upsert_user(self, username: str, role: str, password: str, enabled: bool = True) -> dict:
        username = username.strip().lower()
        if (
            username == "admin"
            or not username
            or not username.replace("_", "").replace("-", "").isalnum()
        ):
            raise UserManagementError("invalid or reserved username")
        if role not in ROLE_PERMISSIONS:
            raise UserManagementError("invalid role")
        records = self._user_records()
        existing = next((item for item in records if item["username"] == username), None)
        if not existing and not password:
            raise UserManagementError("new user requires a password")
        password_value = password_record(password) if password else existing["password"]
        record = {
            "username": username,
            "role": role,
            "enabled": enabled,
            "password": password_value,
        }
        records = [item for item in records if item["username"] != username] + [record]
        self._write_users(records)
        if not enabled:
            self._revoke_actor_sessions(username)
        return {key: value for key, value in record.items() if key != "password"}

    def delete_user(self, username: str) -> None:
        username = username.strip().lower()
        if username == "admin":
            raise UserManagementError("built-in administrator cannot be deleted")
        records = self._user_records()
        if not any(item["username"] == username for item in records):
            raise UserManagementError("user not found")
        self._write_users([item for item in records if item["username"] != username])
        self._revoke_actor_sessions(username)

    @staticmethod
    def allowed(session: dict[str, Any], permission: str) -> bool:
        permissions = ROLE_PERMISSIONS.get(str(session.get("role")), set())
        return "*" in permissions or permission in permissions

    def valid_csrf(self, session: dict[str, Any], header: str, cookie: str) -> bool:
        expected = str(session.get("csrf") or "")
        return (
            bool(expected and header and cookie)
            and hmac.compare_digest(header, expected)
            and hmac.compare_digest(cookie, expected)
        )

    def _find_user(self, username: str) -> dict | None:
        return next(
            (item for item in self._user_records() if item["username"] == username.lower()), None
        )

    def _user_records(self) -> list[dict]:
        if not self.users_file or not self.users_file.is_file():
            return []
        try:
            value = json.loads(self.users_file.read_text(encoding="utf-8"))
            return value.get("users", []) if isinstance(value, dict) else []
        except (OSError, ValueError):
            return []

    def _write_users(self, records: list[dict]) -> None:
        if not self.users_file:
            raise UserManagementError("user store is not configured")
        self._write_json(self.users_file, {"version": 1, "users": records})

    def _sessions(self) -> dict[str, dict]:
        if not self.session_state_file or not self.session_state_file.is_file():
            return {}
        try:
            value = json.loads(self.session_state_file.read_text(encoding="utf-8"))
            return value.get("sessions", {}) if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _update_session(self, session_id: str, seen: int, revoked: bool, actor: str = "") -> None:
        if not self.session_state_file:
            return
        with self._lock:
            sessions = self._sessions()
            previous = sessions.get(session_id, {})
            sessions[session_id] = {
                "last_seen": seen,
                "revoked": revoked,
                "actor": actor or previous.get("actor", ""),
            }
            cutoff = seen - self.session_absolute_seconds
            sessions = {
                key: item
                for key, item in sessions.items()
                if int(item.get("last_seen", 0)) >= cutoff
            }
            self._write_sessions(sessions)

    def _revoke_actor_sessions(self, actor: str) -> None:
        if not self.session_state_file:
            return
        with self._lock:
            sessions = self._sessions()
            for item in sessions.values():
                if item.get("actor") == actor:
                    item["revoked"] = True
            self._write_sessions(sessions)

    def _write_sessions(self, sessions: dict) -> None:
        if self.session_state_file:
            self._write_json(self.session_state_file, {"version": 1, "sessions": sessions})

    def _keys(self) -> list[str]:
        if self.signing_keys_file and self.signing_keys_file.is_file():
            try:
                keys = json.loads(self.signing_keys_file.read_text(encoding="utf-8")).get(
                    "keys", []
                )
                if keys:
                    return [str(item) for item in keys[:2]]
            except (OSError, ValueError):
                pass
        return [self.session_secret]

    def _sign(self, encoded: str) -> str:
        return self._signature(self._keys()[0], encoded)

    @staticmethod
    def _signature(key: str, encoded: str) -> str:
        return hmac.new(key.encode(), encoded.encode(), hashlib.sha256).hexdigest()

    def _require_configuration(self) -> None:
        if not self.configured():
            raise AuthConfigurationError("admin authentication is not configured")

    def _read_password_record(self) -> dict[str, Any] | None:
        if not self.password_file or not self.password_file.is_file():
            return None
        try:
            value = json.loads(self.password_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) and value.get("scheme") == "scrypt" else None

    @staticmethod
    def _write_json(path: Path, value: dict) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8"
        )
        os.chmod(temporary, 0o600)
        temporary.replace(path)

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    @staticmethod
    def _decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
