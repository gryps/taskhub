import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any


SESSION_COOKIE = "taskhub_v2_session"
CSRF_COOKIE = "taskhub_v2_csrf"
SESSION_TTL_SECONDS = 12 * 60 * 60


class AuthConfigurationError(RuntimeError):
    pass


class AuthService:
    def __init__(self, admin_token: str, session_secret: str):
        self.admin_token = admin_token
        self.session_secret = session_secret

    def configured(self) -> bool:
        return bool(self.admin_token and self.session_secret)

    def valid_admin_token(self, token: str) -> bool:
        self._require_configuration()
        return bool(token) and hmac.compare_digest(token, self.admin_token)

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

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    @staticmethod
    def _decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
