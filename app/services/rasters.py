"""DEM lifecycle (ingest, open, delete) and geometry resolution for terrain analyses."""

import hashlib
import json
import logging
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePath
from time import perf_counter
from typing import Any

import rasterio
import shapely
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import metrics
from app.config import Settings
from app.errors import GeoAPIError, InvalidGeoFile, NotFound, UnsupportedFileType
from app.models import Feature, FileStatus, Raster, User
from app.schemas import FeatureRef
from app.services import queries
from app.services.ingestion import ProcessingFailed
from app.storage import Storage
from app.terrain.ingest import validate_and_convert

log = logging.getLogger(__name__)

RASTER_EXTENSIONS = (".tif", ".tiff")


def check_raster_extension(filename: str) -> None:
    ext = PurePath(filename).suffix.lower()
    if ext not in RASTER_EXTENSIONS:
        raise UnsupportedFileType(
            f"Unsupported file type {ext or '(none)'!r}. Upload a GeoTIFF elevation model (.tif/.tiff)."
        )


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def create_raster_record(
    session: Session, filename: str, upload: Path, owner_id: str | None = None, project_id: str | None = None
) -> Raster:
    filename = PurePath(filename.replace("\\", "/")).name[:255] or "dem.tif"
    check_raster_extension(filename)
    record = Raster(
        filename=filename,
        size_bytes=upload.stat().st_size,
        sha256=_sha256(upload),
        status=FileStatus.PROCESSING,
        owner_id=owner_id,
        project_id=project_id,
    )
    session.add(record)
    session.commit()
    return record


def process_raster_record(
    session: Session, settings: Settings, storage: Storage, record: Raster, upload: Path
) -> Raster:
    """Validate, convert to COG and store. Runs in the request (sync) or a Celery worker."""
    started = perf_counter()
    with tempfile.TemporaryDirectory() as tmp:
        cog = Path(tmp) / "cog.tif"
        try:
            info = validate_and_convert(upload, cog, settings.max_raster_pixels)
            key = f"rasters/{record.id}.tif"
            storage.put(cog, key)
        except GeoAPIError as exc:
            _fail(session, record, exc.message)
            raise ProcessingFailed(exc, record.id) from exc
        except Exception:
            log.exception("Unexpected error ingesting raster %s", record.id)
            _fail(session, record, "Internal error while processing the raster.")
            raise

    record.storage_key = key
    record.crs, record.crs_wkt = info.crs, info.crs_wkt
    record.width, record.height, record.band_count = info.width, info.height, info.band_count
    record.dtype, record.nodata = info.dtype, info.nodata
    record.cell_size_m, record.bbox = list(info.cell_size_m), info.bbox
    record.z_min, record.z_max, record.z_mean = info.z_min, info.z_max, info.z_mean
    record.valid_pct, record.warnings = info.valid_pct, info.warnings
    record.status = FileStatus.COMPLETED
    record.processed_at = datetime.now(UTC)
    record.processing_ms = round((perf_counter() - started) * 1000, 2)
    session.commit()
    metrics.observe_processing("raster", record.status, perf_counter() - started)
    log.info("Ingested DEM %s (%dx%d) in %.0f ms", record.id, info.width, info.height, record.processing_ms)
    return record


def ingest_raster(
    session: Session,
    settings: Settings,
    storage: Storage,
    filename: str,
    upload: Path,
    owner_id: str | None = None,
    project_id: str | None = None,
) -> Raster:
    """Create and process in one go (sync mode)."""
    record = create_raster_record(session, filename, upload, owner_id, project_id)
    return process_raster_record(session, settings, storage, record, upload)


def _fail(session: Session, record: Raster, message: str) -> None:
    metrics.observe_processing("raster", FileStatus.FAILED, 0.0)
    record.status = FileStatus.FAILED
    record.error = message
    record.processed_at = datetime.now(UTC)
    session.commit()


def get_raster(session: Session, raster_id: str, *, ready: bool = True, user: User | None = None) -> Raster:
    record = session.get(Raster, raster_id)
    if record is None or (user is not None and record.owner_id != user.id):
        raise NotFound(f"Raster {raster_id!r} not found.")
    if ready and (record.status != FileStatus.COMPLETED or not record.storage_key):
        raise InvalidGeoFile(f"Raster {raster_id!r} is not ready ({record.status}).")
    return record


@contextmanager
def open_raster(storage: Storage, record: Raster) -> Iterator[rasterio.io.DatasetReader]:
    assert record.storage_key
    # The storage's GDAL settings make /vsis3/ paths readable (endpoint, credentials).
    with storage.rasterio_env(), rasterio.open(storage.gdal_path(record.storage_key)) as ds:
        yield ds


def delete_raster(session: Session, storage: Storage, record: Raster) -> None:
    if record.storage_key:
        storage.delete(record.storage_key)
    session.delete(record)
    session.commit()


def resolve_geometry(
    session: Session, geometry: dict[str, Any] | None, feature: FeatureRef | None, user: User | None = None
) -> BaseGeometry:
    """A WGS84 shapely geometry from inline GeoJSON or a stored feature."""
    if (geometry is None) == (feature is None):
        raise GeoAPIError("Provide exactly one of 'geometry' or 'feature'.")
    if geometry is not None:
        try:
            geom = shape(geometry)
        except Exception as exc:
            raise GeoAPIError(f"Invalid GeoJSON geometry: {exc}") from exc
        minx, miny, maxx, maxy = geom.bounds
        if not (-180 <= minx <= maxx <= 180 and -90 <= miny <= maxy <= 90):
            raise GeoAPIError("Geometry must be WGS84 longitude/latitude.")
        return shapely.force_2d(geom)
    assert feature is not None
    queries.get_file(session, feature.file_id, user)  # ownership check
    row = session.scalar(
        select(Feature).where(Feature.file_id == feature.file_id, Feature.index == feature.index)
    )
    if row is None:
        raise NotFound(f"Feature {feature.index} not found in file {feature.file_id!r}.")
    if not row.geometry_wgs84:
        raise GeoAPIError("That feature has no geometry that can be placed on Earth.")
    return shapely.force_2d(shape(json.loads(row.geometry_wgs84)))
