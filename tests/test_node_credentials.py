import json

import pytest
from cryptography.fernet import Fernet

from taskhub_v2.domain.models import NodeDefinition
from taskhub_v2.execution.runner import NodeRunner
from taskhub_v2.security.encryption import SecretCipher
from taskhub_v2.security.node_credentials import NodeCredentialError, NodeCredentialVault


def vault(tmp_path):
    return NodeCredentialVault(
        str(tmp_path / "node-credentials.json"),
        SecretCipher(Fernet.generate_key().decode()),
    )


def test_credentials_are_unique_encrypted_and_not_resolved_across_nodes(tmp_path):
    subject = vault(tmp_path)
    first = subject.issue("worker-01")
    second = subject.issue("worker-02")

    assert first != second
    assert subject.resolve("worker-01") == first
    assert subject.resolve("worker-02") == second
    stored = (tmp_path / "node-credentials.json").read_text()
    assert first not in stored
    assert second not in stored
    assert json.loads(stored)["credentials"]["worker-01"]["ciphertext"]


def test_rotate_invalidates_previous_value_and_increments_version(tmp_path):
    subject = vault(tmp_path)
    old = subject.issue("worker-01")
    new = subject.rotate("worker-01")

    assert new != old
    assert subject.resolve("worker-01") == new
    assert subject.metadata("worker-01")["version"] == 2


def test_revoke_removes_ciphertext_and_prevents_resolution(tmp_path):
    subject = vault(tmp_path)
    subject.issue("worker-01")
    subject.revoke("worker-01")

    assert subject.resolve("worker-01") == ""
    assert subject.metadata("worker-01")["status"] == "revoked"
    payload = json.loads((tmp_path / "node-credentials.json").read_text())
    assert payload["credentials"]["worker-01"]["ciphertext"] == ""
    with pytest.raises(NodeCredentialError):
        subject.rotate("worker-01")


def test_runner_selects_only_the_target_nodes_credential(tmp_path):
    subject = vault(tmp_path)
    first = subject.issue("worker-01")
    second = subject.issue("worker-02")
    runner = NodeRunner("legacy", token_resolver=subject.resolve)

    assert runner._headers(NodeDefinition(id="worker-01", kind="remote", url="http://one")) == {
        "Authorization": f"Bearer {first}"
    }
    assert runner._headers(NodeDefinition(id="worker-02", kind="remote", url="http://two")) == {
        "Authorization": f"Bearer {second}"
    }
