"""Initial schema: vector files and features, elevation rasters.

On PostgreSQL it also enables PostGIS and keeps a native ``features.geom``
geometry column (SRID 4326) with a GiST index, filled at ingestion. It powers
``ST_AsMVT`` vector tiles and indexed spatial queries. SQLite gets the same
tables without it; the portable ``minx..maxy`` columns cover spatial filtering
there.

Revision ID: 0001
Revises:
Create Date: 2026-10-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FILE_STATUS = sa.Enum("PROCESSING", "COMPLETED", "FAILED", name="filestatus", native_enum=False, length=16)
MEASUREMENT_STATUS = sa.Enum(
    "MEASURED",
    "NOT_APPLICABLE",
    "UNSUPPORTED",
    "FAILED",
    name="measurementstatus",
    native_enum=False,
    length=16,
)


def upgrade() -> None:
    op.create_table(
        "files",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("file_format", sa.String(16), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("status", FILE_STATUS, nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("crs", sa.String(64), nullable=True),
        sa.Column("feature_count", sa.Integer(), nullable=False),
        sa.Column("layers", sa.JSON(), nullable=False),
        sa.Column("bbox", sa.JSON(), nullable=True),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("processing_ms", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_files_sha256", "files", ["sha256"])

    op.create_table(
        "features",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("file_id", sa.String(36), sa.ForeignKey("files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("index", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.String(255), nullable=True),
        sa.Column("layer", sa.String(255), nullable=True),
        sa.Column("geometry_type", sa.String(32), nullable=True),
        sa.Column("geometry", sa.Text(), nullable=True),
        sa.Column("geometry_wgs84", sa.Text(), nullable=True),
        sa.Column("minx", sa.Float(), nullable=True),
        sa.Column("miny", sa.Float(), nullable=True),
        sa.Column("maxx", sa.Float(), nullable=True),
        sa.Column("maxy", sa.Float(), nullable=True),
        sa.Column("crs", sa.String(64), nullable=True),
        sa.Column("properties", sa.JSON(), nullable=False),
        sa.Column("measurement_status", MEASUREMENT_STATUS, nullable=False),
        sa.Column("area_m2", sa.Float(), nullable=True),
        sa.Column("perimeter_m", sa.Float(), nullable=True),
        sa.Column("length_m", sa.Float(), nullable=True),
        sa.Column("measurement_crs", sa.JSON(), nullable=False),
        sa.Column("measurement_note", sa.Text(), nullable=True),
        sa.UniqueConstraint("file_id", "index", name="uq_feature_file_index"),
    )
    op.create_index("ix_features_file_id", "features", ["file_id"])
    op.create_index("ix_features_file_bbox", "features", ["file_id", "minx", "maxx", "miny", "maxy"])

    op.create_table(
        "rasters",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("status", FILE_STATUS, nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("storage_key", sa.String(255), nullable=True),
        sa.Column("crs", sa.String(64), nullable=True),
        sa.Column("crs_wkt", sa.Text(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("band_count", sa.Integer(), nullable=True),
        sa.Column("dtype", sa.String(16), nullable=True),
        sa.Column("nodata", sa.Float(), nullable=True),
        sa.Column("cell_size_m", sa.JSON(), nullable=True),
        sa.Column("bbox", sa.JSON(), nullable=True),
        sa.Column("z_min", sa.Float(), nullable=True),
        sa.Column("z_max", sa.Float(), nullable=True),
        sa.Column("z_mean", sa.Float(), nullable=True),
        sa.Column("valid_pct", sa.Float(), nullable=True),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("processing_ms", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_rasters_sha256", "rasters", ["sha256"])

    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        op.execute("ALTER TABLE features ADD COLUMN geom geometry(Geometry, 4326)")
        op.execute("CREATE INDEX ix_features_geom ON features USING GIST (geom)")


def downgrade() -> None:
    op.drop_table("rasters")
    op.drop_table("features")
    op.drop_table("files")
