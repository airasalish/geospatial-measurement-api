"""ORM models.

Geometry is stored as GeoJSON text rather than a spatial type so the service runs
on plain SQLite with no extensions. Moving to PostGIS is a column-type change.
"""

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class FileStatus(enum.StrEnum):
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class MeasurementStatus(enum.StrEnum):
    MEASURED = "MEASURED"
    NOT_APPLICABLE = "NOT_APPLICABLE"  # points / null geometry: nothing to measure
    UNSUPPORTED = "UNSUPPORTED"  # geometry type we don't measure (KML Model, MultiPatch)
    FAILED = "FAILED"  # malformed or empty geometry, unknown CRS, projection error


class UploadedFile(Base):
    __tablename__ = "files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    filename: Mapped[str] = mapped_column(String(255))
    file_format: Mapped[str] = mapped_column(String(16))  # "shapefile" | "kml" | "kmz"
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[FileStatus] = mapped_column(Enum(FileStatus, native_enum=False, length=16))
    error: Mapped[str | None] = mapped_column(Text, default=None)
    # Human-readable CRS of the source data ("EPSG:4326"); "MIXED" if layers differ.
    crs: Mapped[str | None] = mapped_column(String(64), default=None)
    feature_count: Mapped[int] = mapped_column(Integer, default=0)
    layers: Mapped[list[str]] = mapped_column(JSON, default=list)
    # WGS84 extent of all mappable features: [min_lon, min_lat, max_lon, max_lat].
    bbox: Mapped[list[float] | None] = mapped_column(JSON, default=None)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    processing_ms: Mapped[float | None] = mapped_column(Float, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    features: Mapped[list["Feature"]] = relationship(
        back_populates="file", cascade="all, delete-orphan", passive_deletes=True
    )


class Feature(Base):
    __tablename__ = "features"
    __table_args__ = (
        UniqueConstraint("file_id", "index", name="uq_feature_file_index"),
        Index("ix_features_file_bbox", "file_id", "minx", "maxx", "miny", "maxy"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), index=True)
    # 0-based position within the file; the stable public identifier of a feature.
    index: Mapped[int] = mapped_column(Integer)
    # Identifier from the source data when it has one (KML Placemark id).
    source_id: Mapped[str | None] = mapped_column(String(255), default=None)
    layer: Mapped[str | None] = mapped_column(String(255), default=None)
    geometry_type: Mapped[str | None] = mapped_column(String(32), default=None)
    # GeoJSON geometry in the *source* CRS, stored as text: it is produced as a string
    # by shapely's vectorised encoder and only parsed when served.
    geometry: Mapped[str | None] = mapped_column(Text, default=None)
    # The same geometry in WGS84 for web maps (None if it can't be placed on Earth).
    geometry_wgs84: Mapped[str | None] = mapped_column(Text, default=None)
    # WGS84 bounding box: a portable spatial prefilter (bbox queries, vector tiles).
    # On PostgreSQL a PostGIS ``geom`` column with a GiST index is also maintained
    # (see migrations); it is not mapped here so the model stays dialect-neutral.
    minx: Mapped[float | None] = mapped_column(Float, default=None)
    miny: Mapped[float | None] = mapped_column(Float, default=None)
    maxx: Mapped[float | None] = mapped_column(Float, default=None)
    maxy: Mapped[float | None] = mapped_column(Float, default=None)
    crs: Mapped[str | None] = mapped_column(String(64), default=None)
    properties: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    measurement_status: Mapped[MeasurementStatus] = mapped_column(
        Enum(MeasurementStatus, native_enum=False, length=16)
    )
    area_m2: Mapped[float | None] = mapped_column(Float, default=None)
    perimeter_m: Mapped[float | None] = mapped_column(Float, default=None)
    length_m: Mapped[float | None] = mapped_column(Float, default=None)
    # Projected CRS (PROJ string) each value was computed in: {"area": ..., "length": ...}
    measurement_crs: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    measurement_note: Mapped[str | None] = mapped_column(Text, default=None)

    file: Mapped[UploadedFile] = relationship(back_populates="features")


class Raster(Base):
    """An uploaded elevation model (DEM/DSM), stored as a Cloud-Optimised GeoTIFF."""

    __tablename__ = "rasters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    filename: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[FileStatus] = mapped_column(Enum(FileStatus, native_enum=False, length=16))
    error: Mapped[str | None] = mapped_column(Text, default=None)
    storage_key: Mapped[str | None] = mapped_column(String(255), default=None)

    crs: Mapped[str | None] = mapped_column(String(64), default=None)
    crs_wkt: Mapped[str | None] = mapped_column(Text, default=None)
    width: Mapped[int | None] = mapped_column(Integer, default=None)
    height: Mapped[int | None] = mapped_column(Integer, default=None)
    band_count: Mapped[int | None] = mapped_column(Integer, default=None)
    dtype: Mapped[str | None] = mapped_column(String(16), default=None)
    nodata: Mapped[float | None] = mapped_column(Float, default=None)
    cell_size_m: Mapped[list[float] | None] = mapped_column(JSON, default=None)
    bbox: Mapped[list[float] | None] = mapped_column(JSON, default=None)
    z_min: Mapped[float | None] = mapped_column(Float, default=None)
    z_max: Mapped[float | None] = mapped_column(Float, default=None)
    z_mean: Mapped[float | None] = mapped_column(Float, default=None)
    valid_pct: Mapped[float | None] = mapped_column(Float, default=None)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    processing_ms: Mapped[float | None] = mapped_column(Float, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
