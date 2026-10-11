"""API response models (the public contract, kept separate from the ORM models)."""

import json
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import FileStatus, MeasurementStatus


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    format: str = Field(validation_alias="file_format")
    size_bytes: int
    sha256: str
    status: FileStatus
    error: str | None
    crs: str | None = Field(description="Source CRS, e.g. 'EPSG:4326'. 'MIXED' if layers differ.")
    feature_count: int
    layers: list[str]
    bbox: list[float] | None = Field(description="WGS84 extent [min_lon, min_lat, max_lon, max_lat].")
    warnings: list[str]
    processing_ms: float | None
    created_at: datetime
    processed_at: datetime | None

    @field_validator("created_at", "processed_at")
    @classmethod
    def _assume_utc(cls, value: datetime | None) -> datetime | None:
        # SQLite drops tzinfo on the way back; timestamps are always written in UTC.
        return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


class Page[T](BaseModel):
    total: int
    limit: int
    offset: int
    items: list[T]


class FeatureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    index: int = Field(description="0-based position of the feature in the file; its identifier.")
    source_id: str | None = Field(description="Identifier from the source data (KML Placemark id).")
    layer: str | None
    geometry_type: str | None
    crs: str | None
    geometry: dict[str, Any] | None = Field(description="GeoJSON geometry in the source CRS.")
    properties: dict[str, Any]

    @field_validator("geometry", mode="before")
    @classmethod
    def _parse_geojson(cls, value: object) -> object:
        return json.loads(value) if isinstance(value, str) else value


class MeasurementOut(BaseModel):
    """One feature: its identity (index, type, source CRS, attributes) and its measurements."""

    model_config = ConfigDict(from_attributes=True)

    index: int = Field(description="0-based position of the feature in the file; its identifier.")
    source_id: str | None = Field(description="Identifier from the source data (KML Placemark id).")
    layer: str | None
    geometry_type: str | None
    crs: str | None = Field(description="CRS of the source geometry, e.g. 'EPSG:4326'.")
    status: MeasurementStatus = Field(validation_alias="measurement_status")
    area_m2: float | None = Field(description="Polygons: area in square metres.")
    perimeter_m: float | None = Field(description="Polygons: perimeter in metres (including holes).")
    length_m: float | None = Field(description="Lines: length in metres.")
    measurement_crs: dict[str, str] = Field(description="Projected CRS (PROJ) each value was computed in.")
    note: str | None = Field(validation_alias="measurement_note")
    properties: dict[str, Any] = Field(description="Attributes from the source file.")
    geometry: dict[str, Any] | None = Field(
        default=None, description="GeoJSON in the source CRS; included with ?include_geometry=true."
    )

    @field_validator("geometry", mode="before")
    @classmethod
    def _parse_geojson(cls, value: object) -> object:
        return json.loads(value) if isinstance(value, str) else value


class MeasurementSummary(BaseModel):
    feature_count: int
    total_area_m2: float
    total_perimeter_m: float
    total_length_m: float
    by_status: dict[str, int]
    by_geometry_type: dict[str, int]


class MeasurementsResponse(BaseModel):
    file_id: str
    filename: str
    crs: str | None = Field(description="Source CRS of the file ('MIXED' if layers differ).")
    units: dict[str, str] = {"area": "m2", "perimeter": "m", "length": "m"}
    summary: MeasurementSummary
    total: int = Field(description="Features matching the filters.")
    limit: int
    offset: int
    items: list[MeasurementOut]


class ErrorDetail(BaseModel):
    code: str
    message: str
    file_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


# ------------------------------------------------------------------ terrain


class RasterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    size_bytes: int
    sha256: str
    status: FileStatus
    error: str | None
    crs: str | None
    width: int | None
    height: int | None
    band_count: int | None
    dtype: str | None
    nodata: float | None
    cell_size_m: list[float] | None = Field(description="Ground cell size [x, y] in metres.")
    bbox: list[float] | None = Field(description="WGS84 extent [min_lon, min_lat, max_lon, max_lat].")
    z_min: float | None
    z_max: float | None
    z_mean: float | None
    valid_pct: float | None = Field(description="Share of cells with an elevation (not nodata).")
    warnings: list[str]
    processing_ms: float | None
    created_at: datetime
    processed_at: datetime | None

    @field_validator("created_at", "processed_at")
    @classmethod
    def _assume_utc(cls, value: datetime | None) -> datetime | None:
        return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


class FeatureRef(BaseModel):
    file_id: str
    index: int = Field(ge=0)


class BaseSpec(BaseModel):
    method: Literal["lowest", "highest", "mean", "plane", "triangulated", "custom", "surface"] = Field(
        default="triangulated",
        description="How the base under the material is estimated. 'surface' compares against another DEM.",
    )
    elevation: float | None = Field(default=None, description="Base elevation for method 'custom' (metres).")
    raster_id: str | None = Field(default=None, description="Second DEM for method 'surface'.")


class VolumeRequest(BaseModel):
    geometry: dict[str, Any] | None = Field(
        default=None, description="GeoJSON Polygon/MultiPolygon in WGS84 (lon/lat)."
    )
    feature: FeatureRef | None = Field(
        default=None, description="Or: a polygon from an uploaded vector file."
    )
    base: BaseSpec = BaseSpec()
    slope_threshold_deg: float = Field(default=30.0, gt=0, lt=90)


class VolumeOut(BaseModel):
    raster_id: str
    base_method: str
    base_elevation: dict[str, float]
    cut_m3: float = Field(description="Material above the base surface (stockpile volume).")
    fill_m3: float = Field(description="Void below the base surface (pit volume).")
    net_m3: float
    area_2d_m2: float = Field(description="Planimetric area of the DEM cells inside the polygon.")
    surface_area_3d_m2: float = Field(description="Terrain surface area, accounting for slope.")
    elevation: dict[str, float]
    max_height_above_base_m: float
    max_depth_below_base_m: float
    slope: dict[str, float]
    cells: dict[str, int]
    coverage_pct: float
    cell_size_m: tuple[float, float]
    cell_method: str
    plane: dict[str, float] | None
    warnings: list[str]


class BatchVolumeRequest(BaseModel):
    file_id: str = Field(description="Vector file whose polygons (e.g. stockpile outlines) are measured.")
    base: BaseSpec = BaseSpec()
    slope_threshold_deg: float = Field(default=30.0, gt=0, lt=90)


class BatchVolumeItem(BaseModel):
    index: int
    name: str | None
    status: Literal["OK", "SKIPPED", "FAILED"]
    message: str | None = None
    result: VolumeOut | None = None


class BatchVolumeOut(BaseModel):
    raster_id: str
    file_id: str
    base_method: str
    totals: dict[str, float]
    measured: int
    skipped: int
    failed: int
    items: list[BatchVolumeItem]


class ProfileRequest(BaseModel):
    geometry: dict[str, Any] | None = Field(default=None, description="GeoJSON LineString in WGS84.")
    feature: FeatureRef | None = None
    max_samples: int = Field(default=1_000, ge=2, le=5_000)


class ProfileOut(BaseModel):
    raster_id: str
    length_m: float
    stats: dict[str, Any]
    distance_m: list[float]
    elevation_m: list[float | None]
    lon: list[float]
    lat: list[float]


class AltitudeOut(BaseModel):
    raster_id: str
    lon: float
    lat: float
    elevation_m: float | None = Field(description="Bilinear elevation; null outside the DEM or on nodata.")
