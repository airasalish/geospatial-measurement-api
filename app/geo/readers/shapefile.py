"""Reader for zipped ESRI Shapefiles.

A zip may hold several shapefiles (each becomes a layer) and may nest them in
folders. Every layer needs .shp + .shx + .dbf. The .prj (CRS) and .cpg (attribute
encoding) are optional.
"""

import datetime as dt
import io
import struct
from collections.abc import Iterator
from decimal import Decimal
from pathlib import PurePosixPath
from typing import Any

import shapefile
from pyproj import CRS
from pyproj.exceptions import CRSError
from shapely.geometry import shape as to_shapely

from app.errors import InvalidGeoFile
from app.geo.archive import ArchiveLimits, SafeZip
from app.geo.types import Dataset, Layer, RawFeature

REQUIRED_PARTS = (".shp", ".shx", ".dbf")
# What pyshp raises on truncated/corrupt binaries.
_READ_ERRORS = (shapefile.ShapefileException, struct.error, ValueError, IndexError, OSError)

# Shapefile type name (minus Z/M suffix) -> GeoJSON type name.
_GEOJSON_TYPE = {"POINT": "Point", "MULTIPOINT": "MultiPoint", "POLYLINE": "LineString", "POLYGON": "Polygon"}


def _declared_type(shp: shapefile.Shape) -> str:
    base = str(shp.shapeTypeName).removesuffix("Z").removesuffix("M")
    return _GEOJSON_TYPE.get(base, base.title())


def _jsonable(value: Any) -> Any:
    """DBF values can be dates, Decimals or bytes. Turn them into JSON-safe values."""
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value.strip()
    return value


def _group_by_stem(names: list[str]) -> dict[str, dict[str, str]]:
    """{"dir/roads": {".shp": "dir/roads.shp", ".dbf": "dir/roads.DBF", ...}}"""
    groups: dict[str, dict[str, str]] = {}
    for name in names:
        path = PurePosixPath(name)
        ext = path.suffix.lower()
        stem = str(path.with_suffix("")).lower()
        groups.setdefault(stem, {})[ext] = name
    return groups


def _read_crs(raw: bytes, warnings: list[str], layer: str) -> CRS | None:
    try:
        return CRS.from_wkt(raw.decode("utf-8", errors="replace"))
    except CRSError:
        warnings.append(f"Layer {layer!r}: .prj could not be parsed; CRS treated as unknown.")
        return None


def _read_encoding(raw: bytes) -> str:
    encoding = raw.decode("ascii", errors="ignore").strip() or "utf-8"
    try:
        "".encode(encoding)
    except LookupError:
        return "utf-8"
    return str(encoding)


def _iter_features(reader: shapefile.Reader, layer: str) -> Iterator[RawFeature]:
    field_names = [f.name for f in reader.fields[1:]]  # fields[0] is the deletion flag
    try:
        for shape_rec in reader.iterShapeRecords():
            shp = shape_rec.shape
            props = {k: _jsonable(v) for k, v in zip(field_names, shape_rec.record, strict=False)}
            declared = _declared_type(shp)
            if shp.shapeType == shapefile.NULL:
                yield RawFeature(None, props, layer=layer, declared_type=None)
                continue
            if shp.shapeType == shapefile.MULTIPATCH:
                yield RawFeature(
                    None, props, layer=layer, declared_type="MultiPatch", unsupported=True,
                    error="MultiPatch (3D surface) geometries are not supported.",
                )  # fmt: skip
                continue
            try:
                geom = to_shapely(shp.__geo_interface__)
            except Exception as exc:  # malformed shape: skip this feature only, keep the rest
                yield RawFeature(None, props, layer=layer, declared_type=declared, error=str(exc))
                continue
            yield RawFeature(geom, props, layer=layer, declared_type=geom.geom_type)
    except _READ_ERRORS as exc:
        raise InvalidGeoFile(f"Layer {layer!r} is corrupt: {exc}") from exc


def read_shapefile_zip(data: bytes, limits: ArchiveLimits) -> Dataset:
    archive = SafeZip(data, limits)
    groups = _group_by_stem(list(archive.members))
    shp_groups = {stem: parts for stem, parts in groups.items() if ".shp" in parts}

    if not shp_groups:
        raise InvalidGeoFile("Zip archive does not contain a .shp file.")

    layers: list[Layer] = []
    for _, parts in sorted(shp_groups.items()):
        name = PurePosixPath(parts[".shp"]).stem
        missing = [ext for ext in REQUIRED_PARTS if ext not in parts]
        if missing:
            raise InvalidGeoFile(f"Shapefile {name!r} is missing required part(s): {', '.join(missing)}.")

        warnings: list[str] = []
        crs = _read_crs(archive.read(parts[".prj"]), warnings, name) if ".prj" in parts else None
        if ".prj" not in parts:
            warnings.append(f"Layer {name!r} has no .prj; CRS is unknown.")
        encoding = _read_encoding(archive.read(parts[".cpg"])) if ".cpg" in parts else "utf-8"

        try:
            reader = shapefile.Reader(
                shp=io.BytesIO(archive.read(parts[".shp"])),
                shx=io.BytesIO(archive.read(parts[".shx"])),
                dbf=io.BytesIO(archive.read(parts[".dbf"])),
                encoding=encoding,
                encodingErrors="replace",
            )
        except _READ_ERRORS as exc:
            raise InvalidGeoFile(f"Shapefile {name!r} could not be read: {exc}") from exc

        layers.append(Layer(name=name, crs=crs, features=_iter_features(reader, name), warnings=warnings))

    return Dataset(file_format="shapefile", layers=layers)
