from taskhub_v2.node_agent.browser_capabilities import browser_prerequisites


def test_unconfigured_browser_reports_configuration_action(monkeypatch):
    monkeypatch.delenv("TASKHUB_BROWSER_PROFILE_DIR", raising=False)
    monkeypatch.delenv("TASKHUB_BROWSER_AUTH_TARGET", raising=False)

    result = browser_prerequisites()

    assert result["profile_configured"] is False
    assert "update-browser-prerequisites.ps1" in result["action"]["command"]


def test_configured_browser_reports_exact_authorization_action(tmp_path, monkeypatch):
    marker = tmp_path / "ready.json"
    monkeypatch.setenv("TASKHUB_BROWSER_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("TASKHUB_BROWSER_AUTH_TARGET", "https://shop.example/login")
    monkeypatch.setenv("TASKHUB_BROWSER_AUTH_READY_FILE", str(marker))

    pending = browser_prerequisites()

    assert pending["profile_configured"] is True
    assert pending["authenticated"] is False
    assert str(tmp_path) in pending["action"]["command"]
    assert "https://shop.example/login" in pending["action"]["command"]

    marker.write_text("{}", encoding="utf-8")
    assert browser_prerequisites()["action"] is None
