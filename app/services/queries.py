"""Read-side queries. Aggregates run in SQL, so summaries stay cheap for large files."""

import json
from collections.abc import Iterator, Sequence
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.errors import NotFound
from app.models import Feature, MeasurementStatus, UploadedFile
from app.schemas import MeasurementSummary


def get_file(session: Session, file_id: str) -> UploadedFile:
    record = session.get(UploadedFile, file_id)
    if record is None:
        raise NotFound(f"File {file_id!r} not found.")
    return record


def list_files(session: Session, limit: int, offset: int) -> tuple[int, Sequence[UploadedFile]]:
    total = session.scalar(select(func.count()).select_from(UploadedFile)) or 0
    rows = session.scalars(
        select(UploadedFile).order_by(UploadedFile.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return total, rows


BBox = tuple[float, float, float, float]


def _feature_query(
    file_id: str, status: MeasurementStatus | None, geometry_type: str | None, bbox: BBox | None = None
) -> Select[Feature]:
    stmt = select(Feature).where(Feature.file_id == file_id)
    if bbox is not None:
        # Bounding-box overlap: index-friendly and the same on SQLite and PostGIS.
        minx, miny, maxx, maxy = bbox
        stmt = stmt.where(
            Feature.maxx >= minx, Feature.minx <= maxx, Feature.maxy >= miny, Feature.miny <= maxy
        )
    if status is not None:
        stmt = stmt.where(Feature.measurement_status == status)
    if geometry_type is not None:
        stmt = stmt.where(func.lower(Feature.geometry_type) == geometry_type.lower())
    return stmt


def list_features(
    session: Session,
    file_id: str,
    limit: int,
    offset: int,
    status: MeasurementStatus | None = None,
    geometry_type: str | None = None,
    bbox: BBox | None = None,
) -> tuple[int, Sequence[Feature]]:
    stmt = _feature_query(file_id, status, geometry_type, bbox)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(stmt.order_by(Feature.index).limit(limit).offset(offset)).all()
    return total, rows


_NAME_KEYS = ("name", "title", "label")
_ID_SUFFIXES = ("_no", "_id", "_code", "id", "no")


def display_name(properties: dict[str, Any] | None) -> str | None:
    """A human label for a feature: a name-like attribute, else an id-like one."""
    if not properties:
        return None
    lowered = {k.lower(): v for k, v in properties.items()}
    for key in _NAME_KEYS:
        if isinstance(value := lowered.get(key), str) and value.strip():
            return value.strip()
    for key, value in lowered.items():
        if key.endswith(_ID_SUFFIXES) and isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return None


def geojson_chunks(session: Session, file_id: str, limit: int) -> Iterator[str]:
    """Stream a WGS84 GeoJSON FeatureCollection with measurements as properties.

    Geometry is already stored as GeoJSON text, so it is spliced in without being
    parsed. The response streams from a server-side cursor, so memory stays flat
    even for a 250k-feature file.
    """
    columns = (
        Feature.index, Feature.geometry_wgs84, Feature.geometry_type, Feature.measurement_status,
        Feature.area_m2, Feature.perimeter_m, Feature.length_m, Feature.layer, Feature.source_id,
        Feature.properties,
    )  # fmt: skip
    stmt = select(*columns).where(Feature.file_id == file_id).order_by(Feature.index).limit(limit)
    yield '{"type":"FeatureCollection","features":['
    first = True
    for row in session.execute(stmt.execution_options(yield_per=2_000)):
        props = {
            "index": row.index,
            "name": display_name(row.properties),
            "geometry_type": row.geometry_type,
            "status": str(row.measurement_status),
            "area_m2": row.area_m2,
            "perimeter_m": row.perimeter_m,
            "length_m": row.length_m,
            "layer": row.layer,
            "source_id": row.source_id,
        }
        sep = "" if first else ","
        first = False
        yield (
            f'{sep}{{"type":"Feature","id":{row.index},"geometry":{row.geometry_wgs84 or "null"},'
            f'"properties":{json.dumps(props, separators=(",", ":"))}}}'
        )
    yield "]}"


def get_feature(session: Session, file_id: str, index: int) -> Feature:
    feature = session.scalar(select(Feature).where(Feature.file_id == file_id, Feature.index == index))
    if feature is None:
        raise NotFound(f"Feature {index} not found in file {file_id!r}.")
    return feature


def measurement_summary(session: Session, file_id: str) -> MeasurementSummary:
    totals = session.execute(
        select(
            func.count(),
            func.coalesce(func.sum(Feature.area_m2), 0.0),
            func.coalesce(func.sum(Feature.perimeter_m), 0.0),
            func.coalesce(func.sum(Feature.length_m), 0.0),
        ).where(Feature.file_id == file_id)
    ).one()
    by_status = session.execute(
        select(Feature.measurement_status, func.count())
        .where(Feature.file_id == file_id)
        .group_by(Feature.measurement_status)
    ).all()
    by_type = session.execute(
        select(Feature.geometry_type, func.count())
        .where(Feature.file_id == file_id)
        .group_by(Feature.geometry_type)
    ).all()
    return MeasurementSummary(
        feature_count=totals[0],
        total_area_m2=round(totals[1] or 0.0, 3),
        total_perimeter_m=round(totals[2] or 0.0, 3),
        total_length_m=round(totals[3] or 0.0, 3),
        by_status={str(s): n for s, n in by_status},
        by_geometry_type={t or "(none)": n for t, n in by_type},
    )
