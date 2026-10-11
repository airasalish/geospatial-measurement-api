"""Upload processing pipeline: read -> extract features -> measure -> persist.

Kept separate from the HTTP layer so it can move to a background worker
(Celery/RQ/arq) later without changes. The worker would call ``process`` with an id.
"""

import hashlib
import logging
from datetime import UTC, datetime
from itertools import batched
from pathlib import PurePath
from time import perf_counter
from typing import Any

import numpy as np
import shapely
from pyproj import CRS
from sqlalchemy import Table
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import GeoAPIError, InvalidGeoFile
from app.geo.archive import ArchiveLimits
from app.geo.crs import describe_crs, to_wgs84_for_display
from app.geo.measure import measure_batch
from app.geo.readers import check_extension, read_dataset
from app.geo.types import Dataset, RawFeature
from app.models import Feature, FileStatus, UploadedFile

log = logging.getLogger(__name__)

BATCH_SIZE = 2_000
FEATURES: Table = Feature.__table__  # type: ignore[assignment]
_FORMAT_BY_EXT = {".zip": "shapefile", ".kml": "kml", ".kmz": "kmz"}


class ProcessingFailed(GeoAPIError):
    """Wraps a processing error and carries the id of the FAILED file record."""

    def __init__(self, cause: GeoAPIError, file_id: str) -> None:
        super().__init__(cause.message)
        self.status_code = cause.status_code
        self.code = cause.code
        self.file_id = file_id


def _clean_filename(filename: str) -> str:
    # Clients may send a path ("C:\\fakepath\\x.kml"); keep only the final component.
    return PurePath(filename.replace("\\", "/")).name[:255] or "upload"


def ingest_upload(session: Session, settings: Settings, filename: str, data: bytes) -> UploadedFile:
    filename = _clean_filename(filename)
    ext = check_extension(filename)
    record = UploadedFile(
        filename=filename,
        file_format=_FORMAT_BY_EXT[ext],
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        status=FileStatus.PROCESSING,
    )
    session.add(record)
    session.commit()  # the record exists (and is visible) even if processing fails

    started = perf_counter()
    try:
        limits = ArchiveLimits(
            max_members=settings.max_archive_members,
            max_uncompressed_bytes=settings.max_uncompressed_bytes,
            max_compression_ratio=settings.max_compression_ratio,
        )
        dataset = read_dataset(filename, data, limits)
        _persist_features(session, settings, record, dataset)
    except GeoAPIError as exc:
        session.rollback()
        _mark_failed(session, record, exc.message)
        raise ProcessingFailed(exc, record.id) from exc
    except Exception:
        session.rollback()
        log.exception("Unexpected error processing file %s", record.id)
        _mark_failed(session, record, "Internal error while processing the file.")
        raise

    record.status = FileStatus.COMPLETED
    record.processed_at = datetime.now(UTC)
    record.processing_ms = round((perf_counter() - started) * 1000, 2)
    session.commit()
    log.info(
        "Processed file %s (%s, %d features) in %.1f ms",
        record.id, record.file_format, record.feature_count, record.processing_ms,
    )  # fmt: skip
    return record


def _persist_features(session: Session, settings: Settings, record: UploadedFile, dataset: Dataset) -> None:
    index = 0
    crs_labels: set[str | None] = set()
    layer_names: dict[str, None] = {}  # ordered set
    warnings: list[str] = []
    extent = _Extent()

    for layer in dataset.layers:
        crs_label = describe_crs(layer.crs)
        crs_labels.add(crs_label)
        # Features are measured and inserted in fixed-size batches. Measurement is
        # vectorised per batch, and memory stays bounded however big the file is.
        for batch in batched(layer.features, BATCH_SIZE):
            if index + len(batch) > settings.max_features:
                raise InvalidGeoFile(f"File has more than {settings.max_features} features.")
            # Core executemany: skips per-row ORM bookkeeping (~3x faster than ORM bulk insert).
            rows = _rows(record.id, index, crs_label, batch, layer.crs, extent)
            session.execute(FEATURES.insert(), rows)
            index += len(batch)
            layer_names.update((raw.layer, None) for raw in batch if raw.layer)
        warnings.extend(layer.warnings)

    if len(crs_labels) > 1:
        warnings.append("Layers use different CRSs; see each feature's crs.")
    if index == 0:
        warnings.append("File contains no features.")
    record.feature_count = index
    record.crs = crs_labels.pop() if len(crs_labels) == 1 else ("MIXED" if crs_labels else None)
    record.layers = list(layer_names)
    record.warnings = warnings
    record.bbox = extent.as_list()


class _Extent:
    """Running WGS84 bounding box across batches."""

    def __init__(self) -> None:
        self.bounds: np.ndarray | None = None

    def add(self, geoms: np.ndarray) -> None:
        b = shapely.bounds(geoms)  # None/empty -> NaN rows, dropped next
        b = b[np.isfinite(b).all(axis=1)]
        if not len(b):
            return
        batch = np.array([b[:, 0].min(), b[:, 1].min(), b[:, 2].max(), b[:, 3].max()])
        if self.bounds is None:
            self.bounds = batch
        else:
            self.bounds = np.concatenate([np.minimum(self.bounds[:2], batch[:2]),
                                          np.maximum(self.bounds[2:], batch[2:])])  # fmt: skip

    def as_list(self) -> list[float] | None:
        return None if self.bounds is None else [round(float(v), 7) for v in self.bounds]


def _rows(
    file_id: str,
    start: int,
    crs_label: str | None,
    batch: tuple[RawFeature, ...],
    crs: CRS | None,
    extent: _Extent,
) -> list[dict[str, Any]]:
    measurements = measure_batch(batch, crs)
    geoms = np.asarray([raw.geometry for raw in batch], dtype=object)
    display = to_wgs84_for_display(geoms, crs)
    extent.add(display)
    # Vectorised GeoJSON encoding for the whole batch (much faster than per-feature mapping()).
    geojson = shapely.to_geojson(geoms)
    geojson_wgs84 = shapely.to_geojson(display)
    return [
        {
            "file_id": file_id,
            "index": start + offset,
            "source_id": raw.source_id,
            "layer": raw.layer,
            "geometry_type": raw.geometry.geom_type if raw.geometry is not None else raw.declared_type,
            "geometry": gj,
            "geometry_wgs84": gj84,
            "crs": crs_label,
            "properties": raw.properties,
            "measurement_status": m.status,
            "area_m2": m.area_m2,
            "perimeter_m": m.perimeter_m,
            "length_m": m.length_m,
            "measurement_crs": m.crs,
            "measurement_note": m.note,
        }
        for offset, (raw, m, gj, gj84) in enumerate(
            zip(batch, measurements, geojson, geojson_wgs84, strict=True)
        )
    ]


def _mark_failed(session: Session, record: UploadedFile, message: str) -> None:
    record.status = FileStatus.FAILED
    record.error = message
    record.processed_at = datetime.now(UTC)
    session.commit()
