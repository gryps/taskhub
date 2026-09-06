import hashlib
import sys
from pathlib import Path

import pytest

from taskhub_v2.artifacts.store import ArtifactStore
from taskhub_v2.browser.contract import load_acceptance_contract
from taskhub_v2.node_agent.runtime import normalize_command


def test_windows_portable_commands_are_mapped_to_native_executables():
    assert normalize_command(["npm", "test"], windows=True) == ["npm.cmd", "test"]
    assert normalize_command(["npx", "playwright", "test"], windows=True) == [
        "npx.cmd",
        "playwright",
        "test",
    ]
    mapped_python = normalize_command(["python3", "-V"], windows=True)
    assert mapped_python[0] == str(Path(sys.executable).resolve())


def test_acceptance_contract_requires_dedicated_lane_and_browser_capabilities(tmp_path):
    directory = tmp_path / ".taskhub"
    directory.mkdir()
    (directory / "acceptance.yaml").write_text(
        """
preview:
  command: [python3, -m, app]
browsers: [chromium, edge]
command: [npx, playwright, test]
""",
        encoding="utf-8",
    )
    contract = load_acceptance_contract(tmp_path)
    assert contract.workload == "browser_acceptance"
    assert {"chromium", "edge", "windows_gui", "trace"} <= contract.required_capabilities


def test_artifact_store_verifies_agent_digest_and_size(tmp_path):
    store = ArtifactStore(str(tmp_path))
    payload = b"trace"
    artifact = store.write_bytes(
        "run-1", "trace.zip", "browser-trace", payload,
        expected_sha256=hashlib.sha256(payload).hexdigest(),
    )
    assert artifact.metadata["verified"] is True
    with pytest.raises(ValueError, match="digest mismatch"):
        store.write_bytes("run-1", "bad.zip", "trace", payload, expected_sha256="0" * 64)
    with pytest.raises(ValueError, match="size limit"):
        store.write_bytes("run-1", "large.zip", "trace", payload, max_bytes=2)
