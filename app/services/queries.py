"""Read-side queries. Aggregates run in SQL, so summaries stay cheap for large files."""

from collections.abc import Sequence

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


def _feature_query(
    file_id: str, status: MeasurementStatus | None, geometry_type: str | None
) -> Select[Feature]:
    stmt = select(Feature).where(Feature.file_id == file_id)
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
) -> tuple[int, Sequence[Feature]]:
    stmt = _feature_query(file_id, status, geometry_type)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(stmt.order_by(Feature.index).limit(limit).offset(offset)).all()
    return total, rows


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
