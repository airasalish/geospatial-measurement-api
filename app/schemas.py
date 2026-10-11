"""API response models (the public contract, kept separate from the ORM models)."""

import json
from datetime import datetime
from typing import Any

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
    warnings: list[str]
    processing_ms: float | None
    created_at: datetime
    processed_at: datetime | None


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
    model_config = ConfigDict(from_attributes=True)

    index: int
    source_id: str | None
    layer: str | None
    geometry_type: str | None
    status: MeasurementStatus = Field(validation_alias="measurement_status")
    area_m2: float | None
    perimeter_m: float | None
    length_m: float | None
    measurement_crs: dict[str, str] = Field(description="Projected CRS (PROJ) used for each value.")
    note: str | None = Field(validation_alias="measurement_note")


class MeasurementSummary(BaseModel):
    feature_count: int
    total_area_m2: float
    total_perimeter_m: float
    total_length_m: float
    by_status: dict[str, int]
    by_geometry_type: dict[str, int]


class MeasurementsResponse(BaseModel):
    file_id: str
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
