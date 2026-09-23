from pathlib import Path


def test_windows_installer_prefers_a_writable_fixed_d_drive():
    script = (
        Path(__file__).parents[1] / "deploy" / "windows" / "install-node-agent.ps1"
    ).read_text(encoding="utf-8")

    assert '[string]$WorkRoot = ""' in script
    assert "$DataDrive.DriveType -eq 3" in script
    assert "$DataDrive.FreeSpace -gt 1GB" in script
    assert '$Jobs = "D:\\TaskHub\\jobs"' in script
    assert '$Jobs = Join-Path $Root "jobs"' in script
    assert 'TASKHUB_NODE_WORK_ROOT = "$Jobs"' in script
    assert 'TASKHUB_NODE_CACHE_ROOT = "$CacheRoot"' in script
