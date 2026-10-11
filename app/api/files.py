"""/api/files endpoints."""

from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile, status
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.config import Settings
from app.errors import FileTooLarge
from app.geo.readers import check_extension
from app.models import MeasurementStatus
from app.schemas import (
    ErrorResponse,
    FeatureOut,
    FileOut,
    MeasurementOut,
    MeasurementsResponse,
    Page,
)
from app.services import queries
from app.services.ingestion import ingest_upload

router = APIRouter(prefix="/api/files", tags=["files"])

UPLOAD_CHUNK = 1024 * 1024


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_session(request: Request) -> Iterator[Session]:
    yield from request.app.state.db.session()


SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def pagination(
    settings: SettingsDep,
    limit: Annotated[int | None, Query(ge=1, description="Page size.")] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> tuple[int, int]:
    return min(limit or settings.default_page_size, settings.max_page_size), offset


PageDep = Annotated[tuple[int, int], Depends(pagination)]

_errors: dict[int | str, dict[str, Any]] = {
    code: {"model": ErrorResponse}
    for code in (status.HTTP_404_NOT_FOUND, status.HTTP_413_CONTENT_TOO_LARGE,
                 status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, status.HTTP_422_UNPROCESSABLE_CONTENT)
}  # fmt: skip


async def _read_upload(upload: UploadFile, max_bytes: int) -> bytes:
    """Read the upload in chunks and stop as soon as it goes over the limit."""
    chunks: list[bytes] = []
    size = 0
    while chunk := await upload.read(UPLOAD_CHUNK):
        size += len(chunk)
        if size > max_bytes:
            raise FileTooLarge(f"File exceeds the {max_bytes // (1024 * 1024)} MB upload limit.")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=FileOut,
    responses=_errors,
    summary="Upload and process a geospatial file",
)
async def upload_file(
    request: Request,
    response: Response,
    settings: SettingsDep,
    file: Annotated[UploadFile, File(description=".zip (Shapefile), .kml or .kmz")],
) -> FileOut:
    filename = file.filename or ""
    check_extension(filename)  # reject before reading the body
    data = await _read_upload(file, settings.max_upload_bytes)

    # Parsing and reprojection are CPU-bound. Run them in the thread pool so the
    # event loop stays free for other requests.
    def _process() -> FileOut:
        with request.app.state.db.session_factory() as session:
            record = ingest_upload(session, settings, filename, data)
            return FileOut.model_validate(record)

    out = await run_in_threadpool(_process)
    response.headers["Location"] = str(request.url_for("get_file", file_id=out.id))
    return out


@router.get("/", response_model=Page[FileOut], summary="List uploaded files")
def list_files(session: SessionDep, page: PageDep) -> Page[FileOut]:
    limit, offset = page
    total, rows = queries.list_files(session, limit, offset)
    return Page[FileOut](
        total=total, limit=limit, offset=offset, items=[FileOut.model_validate(r) for r in rows]
    )


@router.get("/{file_id}/", response_model=FileOut, responses=_errors, summary="File information")
def get_file(file_id: str, session: SessionDep) -> FileOut:
    return FileOut.model_validate(queries.get_file(session, file_id))


@router.delete(
    "/{file_id}/", status_code=status.HTTP_204_NO_CONTENT, responses=_errors, summary="Delete a file"
)
def delete_file(file_id: str, session: SessionDep) -> Response:
    session.delete(queries.get_file(session, file_id))
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{file_id}/features/",
    response_model=Page[FeatureOut],
    responses=_errors,
    summary="Features with geometry, CRS and properties",
)
def list_features(
    file_id: str,
    session: SessionDep,
    page: PageDep,
    geometry_type: Annotated[str | None, Query(description="e.g. Polygon")] = None,
) -> Page[FeatureOut]:
    queries.get_file(session, file_id)
    limit, offset = page
    total, rows = queries.list_features(session, file_id, limit, offset, geometry_type=geometry_type)
    return Page[FeatureOut](
        total=total, limit=limit, offset=offset, items=[FeatureOut.model_validate(r) for r in rows]
    )


@router.get(
    "/{file_id}/features/{index}/",
    response_model=FeatureOut,
    responses=_errors,
    summary="A single feature",
)
def get_feature(file_id: str, index: int, session: SessionDep) -> FeatureOut:
    return FeatureOut.model_validate(queries.get_feature(session, file_id, index))


@router.get(
    "/{file_id}/measurements/",
    response_model=MeasurementsResponse,
    responses=_errors,
    summary="Per-feature measurements and file totals",
)
def list_measurements(
    file_id: str,
    session: SessionDep,
    page: PageDep,
    status_filter: Annotated[MeasurementStatus | None, Query(alias="status")] = None,
    geometry_type: Annotated[str | None, Query(description="e.g. Polygon")] = None,
) -> MeasurementsResponse:
    queries.get_file(session, file_id)
    limit, offset = page
    total, rows = queries.list_features(session, file_id, limit, offset, status_filter, geometry_type)
    return MeasurementsResponse(
        file_id=file_id,
        summary=queries.measurement_summary(session, file_id),
        total=total,
        limit=limit,
        offset=offset,
        items=[MeasurementOut.model_validate(r) for r in rows],
    )
