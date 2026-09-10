from cryptography.fernet import Fernet, InvalidToken


class SecretEncryptionError(ValueError):
    pass


class SecretCipher:
    def __init__(self, key: str):
        try:
            self._fernet = Fernet(key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise SecretEncryptionError(
                "TASKHUB_CONFIG_ENCRYPTION_KEY must be a URL-safe Fernet key"
            ) from exc

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, UnicodeEncodeError) as exc:
            raise SecretEncryptionError("managed secret cannot be decrypted") from exc


def build_secret_cipher(key: str) -> SecretCipher | None:
    return SecretCipher(key) if key else None
