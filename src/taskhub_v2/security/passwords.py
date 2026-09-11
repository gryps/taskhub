import base64
import hashlib
import hmac
import secrets

PASSWORD_MIN_LENGTH = 10
PASSWORD_SCRYPT_N = 2**14
PASSWORD_SCRYPT_R = 8
PASSWORD_SCRYPT_P = 1


def password_record(password: str) -> dict:
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"password must contain at least {PASSWORD_MIN_LENGTH} characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(),
        salt=salt,
        n=PASSWORD_SCRYPT_N,
        r=PASSWORD_SCRYPT_R,
        p=PASSWORD_SCRYPT_P,
        dklen=32,
    )
    return {
        "scheme": "scrypt",
        "n": PASSWORD_SCRYPT_N,
        "r": PASSWORD_SCRYPT_R,
        "p": PASSWORD_SCRYPT_P,
        "salt": _encode(salt),
        "hash": _encode(digest),
    }


def verify_password(secret: str, record: dict) -> bool:
    try:
        password = record.get("password", record)
        expected = _decode(password["hash"])
        candidate = hashlib.scrypt(
            secret.encode(),
            salt=_decode(password["salt"]),
            n=int(password["n"]),
            r=int(password["r"]),
            p=int(password["p"]),
            dklen=len(expected),
        )
        return hmac.compare_digest(candidate, expected)
    except (KeyError, TypeError, ValueError):
        return False


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
