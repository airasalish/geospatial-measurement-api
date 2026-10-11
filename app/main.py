"""FastAPI application factory."""

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.datastructures import URL
from starlette.types import Receive, Scope, Send

from app import metrics
from app.api.auth import router as auth_router
from app.api.files import router as files_router
from app.api.rasters import router as rasters_router
from app.config import DEV_JWT_SECRET, Settings, get_settings, resolve_path
from app.db import Database
from app.errors import GeoAPIError
from app.schemas import ErrorDetail, ErrorResponse
from app.storage import make_storage

log = logging.getLogger(__name__)


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


class JsonFormatter(logging.Formatter):
    """One JSON object per line, for log shippers (Loki, CloudWatch, ...)."""

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out)


def configure_logging(fmt: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        JsonFormatter()
        if fmt == "json"
        else logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    if settings.auth_required and settings.jwt_secret == DEV_JWT_SECRET:
        raise RuntimeError("GEO_AUTH_REQUIRED is on: set GEO_JWT_SECRET to a long random value.")
    if settings.auth_required and len(settings.jwt_secret.encode()) < 32:
        raise RuntimeError("GEO_JWT_SECRET must be at least 32 bytes for HS256 (RFC 7518).")
    db = Database(settings.database_url)
    storage = make_storage(settings, resolve_path(settings.storage_dir))

    if settings.task_mode == "celery":
        from app import worker

        worker.celery.conf.task_always_eager = settings.celery_eager
        worker.register(db, storage)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        db.migrate()
        yield
        db.engine.dispose()

    app = FastAPI(
        title="Traverse · Geospatial File Measurement API",
        version="1.1.0",
        description=(
            "Upload a Shapefile (.zip), KML or KMZ: every feature is measured (area, perimeter, length) "
            "in metres using feature-centred projections. Upload a DEM/DSM GeoTIFF: cut/fill volumes, "
            "survey-to-survey change, 3D surface area, slope and elevation profiles."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.db = db
    app.state.storage = storage
    app.middleware("http")(metrics.http_metrics)

    @app.exception_handler(GeoAPIError)
    async def _geo_error(_: Request, exc: GeoAPIError) -> JSONResponse:
        body = ErrorResponse(
            error=ErrorDetail(code=exc.code, message=exc.message, file_id=getattr(exc, "file_id", None))
        )
        return JSONResponse(status_code=exc.status_code, content=body.model_dump(exclude_none=True))

    @app.get("/health", tags=["meta"], summary="Liveness: the process is up")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["meta"], summary="Readiness: database, storage and queue reachable")
    def ready() -> JSONResponse:
        checks: dict[str, str] = {}

        def probe(name: str, fn: Any) -> None:
            try:
                fn()
                checks[name] = "ok"
            except Exception as exc:  # report, don't raise: this endpoint *is* the error report
                checks[name] = f"error: {type(exc).__name__}"
                log.warning("Readiness check %s failed: %s", name, exc)

        def _db() -> None:
            with db.engine.connect() as conn:
                conn.execute(text("SELECT 1"))

        probe("database", _db)
        probe("storage", storage.check)
        if settings.task_mode == "celery" and not settings.celery_eager:
            import redis

            probe("queue", lambda: redis.Redis.from_url(settings.redis_url, socket_timeout=2).ping())
        ok = all(v == "ok" for v in checks.values())
        return JSONResponse(
            {"status": "ok" if ok else "degraded", "checks": checks}, status_code=200 if ok else 503
        )

    @app.get("/metrics", tags=["meta"], include_in_schema=False)
    def prometheus() -> Response:
        return metrics.metrics_response()

    app.include_router(auth_router)
    app.include_router(files_router)
    app.include_router(rasters_router)

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
    if (samples := resolve_path(settings.samples_dir)).is_dir():
        app.mount("/samples", StaticFiles(directory=samples), name="samples")
    if (frontend := resolve_path(settings.frontend_dir)).is_dir():
        app.mount("/", FrontendFiles(directory=frontend, html=True), name="frontend")
    return app


configure_logging(get_settings().log_format)
app = create_app()
