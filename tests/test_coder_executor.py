import base64
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

from app.coder_executor import _prepare_repository, _safe_extract, _validated_diff


def archive_with(name: str, content: bytes) -> str:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
        info = tarfile.TarInfo(name)
        info.size = len(content)
        bundle.addfile(info, io.BytesIO(content))
    return base64.b64encode(buffer.getvalue()).decode()


class CoderExecutorTests(unittest.TestCase):
    def test_source_archive_rejects_parent_traversal(self):
        with tempfile.TemporaryDirectory() as temp_dir, self.assertRaises(HTTPException):
            _safe_extract(archive_with("../secret", b"no"), Path(temp_dir))

    def test_generated_diff_is_git_applicable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.txt").write_text("before\n", encoding="utf-8")
            _prepare_repository(root, "a" * 40)
            (root / "app.txt").write_text("after\n", encoding="utf-8")
            diff = _validated_diff(root)
            self.assertIn("+after", diff)


if __name__ == "__main__":
    unittest.main()
