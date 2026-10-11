"""Format-agnostic structures that every reader produces."""

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from pyproj import CRS
from shapely.geometry.base import BaseGeometry


@dataclass(slots=True)
class RawFeature:
    geometry: BaseGeometry | None
    properties: dict[str, Any]
    source_id: str | None = None
    layer: str | None = None
    # The geometry type as declared by the source. Used when the geometry could not
    # be built (unsupported or malformed) and `geometry` is None.
    declared_type: str | None = None
    # Why the geometry could not be built, if it couldn't.
    error: str | None = None
    unsupported: bool = False


@dataclass
class Layer:
    name: str
    crs: CRS | None
    features: Iterator[RawFeature]
    warnings: list[str] = field(default_factory=list)


@dataclass
class Dataset:
    file_format: str
    layers: list[Layer]
