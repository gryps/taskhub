import hashlib


def backup_key_fingerprint(key: str) -> str:
    if not key:
        return ""
    return hashlib.sha256(f"taskhub-backup-v1:{key}".encode()).hexdigest()
