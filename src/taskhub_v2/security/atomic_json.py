import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def locked_file(path: Path, *, wait_seconds: float = 5, stale_seconds: float = 30):
    """Coordinate read-modify-write operations across processes and service instances."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            break
        except FileExistsError as error:
            try:
                if time.time() - lock_path.stat().st_mtime > stale_seconds:
                    lock_path.unlink()
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"timed out acquiring state lock: {path.name}"
                ) from error
            time.sleep(0.01)
    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def write_json(path: Path, value: dict) -> None:
    """Atomically replace JSON using a unique same-directory temporary file."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            target.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
            target.flush()
            os.fsync(target.fileno())
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
