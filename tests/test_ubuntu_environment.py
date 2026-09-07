from scripts.check_ubuntu_environment import compare


def fixture():
    baseline = {
        "platform": {"os_id": "ubuntu", "os_version": "24.04", "architecture": "x86_64"},
        "tools": {"python": "3.12.3", "pip": "26.2.1", "node": "22.22.1"},
        "venv": "/opt/taskhub/.venv",
    }
    snapshot = {
        **baseline,
        "platform": dict(baseline["platform"]),
        "tools": dict(baseline["tools"]),
        "packages": {"taskhub-v2": "0.1.0", "pip": "26.2.1", "setuptools": "68.1.2"},
    }
    return baseline, snapshot


def test_matching_environment_is_qualified():
    baseline, snapshot = fixture()

    assert compare(baseline, snapshot, {"taskhub-v2": "0.1.0"}) == []


def test_version_drift_and_extra_packages_are_rejected():
    baseline, snapshot = fixture()
    snapshot["tools"]["node"] = "18.19.1"
    snapshot["packages"]["playwright"] = "1.62.0"

    errors = compare(baseline, snapshot, {"taskhub-v2": "0.1.0"})

    assert "tools.node: expected 22.22.1, got 18.19.1" in errors
    assert "unexpected packages: playwright" in errors


def test_missing_required_command_is_rejected(monkeypatch):
    baseline, snapshot = fixture()
    baseline["required_commands"] = ["rg", "codex"]
    monkeypatch.setattr(
        "scripts.check_ubuntu_environment.shutil.which",
        lambda command: "/usr/bin/rg" if command == "rg" else None,
    )

    errors = compare(baseline, snapshot, {"taskhub-v2": "0.1.0"})

    assert "missing commands: codex" in errors
