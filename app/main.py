"""FastAPI application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import URL
from starlette.types import Receive, Scope, Send

from app.api.files import router as files_router
from app.config import Settings, get_settings
from app.db import Database
from app.errors import GeoAPIError
from app.schemas import ErrorDetail, ErrorResponse

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


class FrontendFiles(StaticFiles):
    """Serves the built UI at "/" without swallowing API requests.

    A catch-all mount at "/" matches every path, which would stop FastAPI's
    trailing-slash redirect (``/api/files/x`` -> ``/api/files/x/``) from ever
    running. API paths that reach this mount are handled here instead: redirected
    if a slash is missing, otherwise 404 as JSON, never as an HTML page.
    """

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path: str = scope["path"]
        if scope["type"] == "http" and (path == "/api" or path.startswith("/api/")):
            if not path.endswith("/"):
                url = URL(scope=scope)
                response: JSONResponse | RedirectResponse = RedirectResponse(
                    url.replace(path=path + "/"), status_code=307
                )
            else:
                response = JSONResponse({"detail": "Not Found"}, status_code=404)
            await response(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    db = Database(settings.database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        db.create_all()
        yield
        db.engine.dispose()

    app = FastAPI(
        title="Geospatial File Measurement API",
        version="1.0.0",
        description=(
            "Upload a Shapefile (.zip), KML or KMZ. Features are extracted and measured "
            "(area, perimeter, length) in metres, using feature-centred projections."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.db = db

    @app.exception_handler(GeoAPIError)
    async def _geo_error(_: Request, exc: GeoAPIError) -> JSONResponse:
        body = ErrorResponse(
            error=ErrorDetail(code=exc.code, message=exc.message, file_id=getattr(exc, "file_id", None))
        )
        return JSONResponse(status_code=exc.status_code, content=body.model_dump(exclude_none=True))

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(files_router)

    if origins := [o.strip() for o in settings.cors_origins.split(",") if o.strip()]:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["*"],
            expose_headers=["Location", "X-Total-Count", "X-Truncated"],
        )

    # Sample files for the UI's "try a sample" buttons, and the UI itself. Mounted
    # last so they never shadow API routes.
    if (samples := _resolve(settings.samples_dir)).is_dir():
        app.mount("/samples", StaticFiles(directory=samples), name="samples")
    if (frontend := _resolve(settings.frontend_dir)).is_dir():
        app.mount("/", FrontendFiles(directory=frontend, html=True), name="frontend")
    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
