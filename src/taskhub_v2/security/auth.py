import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

SESSION_COOKIE = "taskhub_v2_session"
CSRF_COOKIE = "taskhub_v2_csrf"
SESSION_TTL_SECONDS = 12 * 60 * 60
PASSWORD_MIN_LENGTH = 10
PASSWORD_SCRYPT_N = 2**14
PASSWORD_SCRYPT_R = 8
PASSWORD_SCRYPT_P = 1


class AuthConfigurationError(RuntimeError):
    pass


class PasswordSetupError(RuntimeError):
    pass


class AuthService:
    def __init__(
        self, admin_token: str, session_secret: str, password_file: str = ""
    ):
        self.admin_token = admin_token
        self.session_secret = session_secret
        self.password_file = Path(password_file) if password_file else None

    def configured(self) -> bool:
        return bool(self.admin_token and self.session_secret)

    def valid_admin_token(self, token: str) -> bool:
        self._require_configuration()
        return bool(token) and hmac.compare_digest(token, self.admin_token)

    def password_login_enabled(self) -> bool:
        return self.password_file is not None

    def setup_required(self) -> bool:
        return bool(self.password_file and not self.password_file.is_file())

    def valid_login_secret(self, secret: str) -> bool:
        self._require_configuration()
        if not self.password_file:
            return self.valid_admin_token(secret)
        record = self._read_password_record()
        if not record or not secret:
            return False
        try:
            salt = self._decode(record["salt"])
            expected = self._decode(record["hash"])
            candidate = hashlib.scrypt(
                secret.encode(),
                salt=salt,
                n=int(record["n"]),
                r=int(record["r"]),
                p=int(record["p"]),
                dklen=len(expected),
            )
        except (KeyError, TypeError, ValueError):
            return False
        return hmac.compare_digest(candidate, expected)

    def initialize_password(self, bootstrap_token: str, password: str) -> None:
        self._require_configuration()
        if not self.password_file:
            raise PasswordSetupError("password login is not enabled")
        if self.password_file.exists():
            raise PasswordSetupError("administrator password is already configured")
        if not self.valid_admin_token(bootstrap_token):
            raise PermissionError("invalid bootstrap token")
        if len(password) < PASSWORD_MIN_LENGTH:
            raise ValueError(
                f"administrator password must contain at least {PASSWORD_MIN_LENGTH} characters"
            )

        salt = secrets.token_bytes(16)
        password_hash = hashlib.scrypt(
            password.encode(),
            salt=salt,
            n=PASSWORD_SCRYPT_N,
            r=PASSWORD_SCRYPT_R,
            p=PASSWORD_SCRYPT_P,
            dklen=32,
        )
        record = {
            "scheme": "scrypt",
            "n": PASSWORD_SCRYPT_N,
            "r": PASSWORD_SCRYPT_R,
            "p": PASSWORD_SCRYPT_P,
            "salt": self._encode(salt),
            "hash": self._encode(password_hash),
        }
        self.password_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(
                self.password_file,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError as error:
            raise PasswordSetupError(
                "administrator password is already configured"
            ) from error
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as target:
                json.dump(record, target, separators=(",", ":"))
                target.write("\n")
                target.flush()
                os.fsync(target.fileno())
        except Exception:
            self.password_file.unlink(missing_ok=True)
            raise

    def create_session(self) -> tuple[str, str]:
        self._require_configuration()
        csrf = secrets.token_urlsafe(24)
        payload = {
            "actor": "admin",
            "csrf": csrf,
            "expires": int(time.time()) + SESSION_TTL_SECONDS,
        }
        encoded = self._encode(json.dumps(payload, separators=(",", ":")).encode())
        signature = hmac.new(
            self.session_secret.encode(), encoded.encode(), hashlib.sha256
        ).hexdigest()
        return f"{encoded}.{signature}", csrf

    def read_session(self, value: str | None) -> dict[str, Any] | None:
        if not self.configured() or not value or "." not in value:
            return None
        encoded, signature = value.rsplit(".", 1)
        expected = hmac.new(
            self.session_secret.encode(), encoded.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        try:
            payload = json.loads(self._decode(encoded))
        except (ValueError, json.JSONDecodeError):
            return None
        return payload if int(payload.get("expires", 0)) > int(time.time()) else None

    def valid_csrf(self, session: dict[str, Any], header: str, cookie: str) -> bool:
        expected = str(session.get("csrf") or "")
        return bool(expected and header and cookie) and hmac.compare_digest(
            header, expected
        ) and hmac.compare_digest(cookie, expected)

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
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    @staticmethod
    def _decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
