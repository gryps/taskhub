from importlib.resources import files
from pathlib import Path

from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles


def mount_canvas(app) -> None:
    packaged = files("taskhub_v2.api").joinpath("canvas")
    source_build = Path(__file__).resolve().parents[3] / "taskhub-web" / "dist"
    directory = Path(str(packaged))
    if not directory.is_dir() and source_build.is_dir():
        directory = source_build
    if directory.is_dir():
        index = directory / "index.html"

        async def canvas_index() -> HTMLResponse:
            return HTMLResponse(
                index.read_text(encoding="utf-8"),
                headers={
                    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )

        app.add_api_route(
            "/canvas/",
            canvas_index,
            methods=["GET"],
            include_in_schema=False,
        )
        app.mount("/canvas", StaticFiles(directory=directory, html=True), name="canvas")
