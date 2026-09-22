import os
import tarfile
from io import BytesIO

from taskhub_v2.execution.runner import NodeRunner


def test_workspace_archive_is_stable_across_file_timestamp_changes(tmp_path):
    script = tmp_path / "scripts" / "check.sh"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    script.chmod(0o755)
    first = NodeRunner._archive(tmp_path)

    os.utime(script, (1_900_000_000, 1_900_000_000))
    second = NodeRunner._archive(tmp_path)

    assert first == second
    with tarfile.open(fileobj=BytesIO(second), mode="r:gz") as bundle:
        member = bundle.getmember("scripts/check.sh")
        assert member.mode & 0o111
        assert bundle.extractfile(member).read() == b"#!/bin/sh\necho ok\n"


def test_workspace_archive_changes_when_file_content_changes(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("first", encoding="utf-8")
    first = NodeRunner._archive(tmp_path)

    source.write_text("second", encoding="utf-8")

    assert NodeRunner._archive(tmp_path) != first
