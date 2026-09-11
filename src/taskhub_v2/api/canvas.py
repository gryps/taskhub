from importlib.resources import files
from pathlib import Path

from fastapi.staticfiles import StaticFiles


def mount_canvas(app) -> None:
    packaged = files("taskhub_v2.api").joinpath("canvas")
    source_build = Path(__file__).resolve().parents[3] / "taskhub-web" / "dist"
    directory = Path(str(packaged))
    if not directory.is_dir() and source_build.is_dir():
        directory = source_build
    if directory.is_dir():
        app.mount("/canvas", StaticFiles(directory=directory, html=True), name="canvas")
