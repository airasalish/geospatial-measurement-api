"""FastAPI application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.files import router as files_router
from app.config import Settings, get_settings
from app.db import Database
from app.errors import GeoAPIError
from app.schemas import ErrorDetail, ErrorResponse


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
    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
