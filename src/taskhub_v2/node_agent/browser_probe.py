"""Probe browsers in the Agent's actual desktop/session, never from flags."""
import json
import os
import tempfile
from pathlib import Path


def probe() -> dict:
    capabilities = dict.fromkeys(
        ("windows_gui", "playwright", "chromium", "edge", "screenshot", "video", "trace"), False
    )
    versions = {}
    if os.name != "nt":
        return {"capabilities": capabilities, "versions": versions}
    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory(prefix="taskhub-browser-probe-") as directory:
        root = Path(directory)
        with sync_playwright() as playwright:
            for name, channel in (("chromium", "chrome"), ("edge", "msedge")):
                browser = None
                try:
                    browser = playwright.chromium.launch(headless=False, channel=channel)
                    context = browser.new_context(record_video_dir=str(root / name))
                    context.tracing.start(screenshots=True, snapshots=True)
                    page = context.new_page()
                    page.set_content("<h1>TaskHub browser capability probe</h1>")
                    page.locator("h1").wait_for(state="visible")
                    screenshot = page.screenshot()
                    trace = root / f"{name}.zip"
                    context.tracing.stop(path=str(trace))
                    video = page.video
                    context.close()
                    video_size = Path(video.path()).stat().st_size
                    if not screenshot or not trace.stat().st_size or not video_size:
                        continue
                    capabilities[name] = True
                    versions[name] = browser.version
                except Exception:
                    continue
                finally:
                    if browser:
                        browser.close()
    working = capabilities["chromium"] and capabilities["edge"]
    for name in ("windows_gui", "playwright", "screenshot", "video", "trace"):
        capabilities[name] = working
    return {"capabilities": capabilities, "versions": versions}


if __name__ == "__main__":
    print(json.dumps(probe()))
