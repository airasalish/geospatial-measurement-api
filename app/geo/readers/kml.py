"""Reader for KML (and KMZ, which is zipped KML).

KML coordinates are always WGS84 longitude/latitude[/altitude] (OGC KML 2.2, section
16.9), so every layer gets EPSG:4326. XML is parsed with defusedxml so a hostile
upload can't use entity expansion (billion laughs) or external entities (XXE).

Placemarks become features. Each one's layer is the path of the Folder/Document
elements that contain it.
"""

import re
from collections.abc import Iterator
from pathlib import PurePosixPath
from xml.etree.ElementTree import Element

import defusedxml.ElementTree as SafeET
from defusedxml import DefusedXmlException
from pyproj import CRS
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.geometry.base import BaseGeometry

from app.errors import InvalidGeoFile
from app.geo.archive import ArchiveLimits, SafeZip
from app.geo.types import Dataset, Layer, RawFeature

WGS84 = CRS.from_epsg(4326)

_CONTAINERS = {"Document", "Folder"}
_SUPPORTED = {"Point", "LineString", "LinearRing", "Polygon", "MultiGeometry"}
# Valid KML geometry elements that we deliberately don't convert.
_UNSUPPORTED = {"Model", "Track", "MultiTrack"}
_COMMA_WS = re.compile(r"\s*,\s*")


class GeometryParseError(ValueError):
    pass


def _local(tag: str) -> str:
    """'{http://www.opengis.net/kml/2.2}Placemark' -> 'Placemark'. Handles KML 2.0-2.2 and gx:."""
    return tag.rsplit("}", 1)[-1]


def _child(elem: Element, name: str) -> Element | None:
    return next((c for c in elem if _local(c.tag) == name), None)


def _children(elem: Element, name: str) -> list[Element]:
    return [c for c in elem if _local(c.tag) == name]


def _text(elem: Element | None) -> str | None:
    if elem is None or elem.text is None:
        return None
    return elem.text.strip() or None


def _parse_coords(elem: Element) -> list[tuple[float, ...]]:
    coords_el = _child(elem, "coordinates")
    raw = _text(coords_el)
    if not raw:
        raise GeometryParseError(f"<{_local(elem.tag)}> has no coordinates.")
    coords: list[tuple[float, ...]] = []
    for token in _COMMA_WS.sub(",", raw).split():
        try:
            values = tuple(float(v) for v in token.split(","))
        except ValueError as exc:
            raise GeometryParseError(f"Invalid coordinate {token!r}.") from exc
        if len(values) not in (2, 3):
            raise GeometryParseError(f"Coordinate {token!r} must be lon,lat[,alt].")
        coords.append(values)
    return coords


def _ring(ring: Element) -> list[tuple[float, ...]]:
    coords = _parse_coords(ring)
    if coords[0] != coords[-1]:
        coords.append(coords[0])  # KML requires closed rings; be lenient and close it
    if len(coords) < 4:
        raise GeometryParseError("A LinearRing needs at least 3 distinct positions.")
    return coords


def _parse_geometry(elem: Element) -> BaseGeometry:
    kind = _local(elem.tag)
    if kind == "Point":
        return Point(_parse_coords(elem)[0])
    if kind in ("LineString", "LinearRing"):
        coords = _parse_coords(elem)
        if len(coords) < 2:
            raise GeometryParseError(f"A {kind} needs at least 2 positions.")
        return LineString(coords)
    if kind == "Polygon":
        outer = _child(elem, "outerBoundaryIs")
        shell = _child(outer, "LinearRing") if outer is not None else None
        if shell is None:
            raise GeometryParseError("Polygon has no <outerBoundaryIs><LinearRing>.")
        # KML allows one LinearRing per <innerBoundaryIs> or several inside one.
        holes = [
            _ring(ring)
            for inner in _children(elem, "innerBoundaryIs")
            for ring in _children(inner, "LinearRing")
        ]
        return Polygon(_ring(shell), holes)
    if kind == "MultiGeometry":
        parts = [_parse_geometry(c) for c in elem if _local(c.tag) in _SUPPORTED]
        return _collect(parts)
    raise GeometryParseError(f"Unsupported geometry <{kind}>.")


def _collect(parts: list[BaseGeometry]) -> BaseGeometry:
    """Collapse a MultiGeometry into the most specific homogeneous Multi* type."""
    flat: list[BaseGeometry] = []
    for p in parts:
        flat.extend(p.geoms if isinstance(p, (MultiPoint, MultiLineString, MultiPolygon)) else [p])
    if not flat:
        return GeometryCollection()
    types = {g.geom_type for g in flat}
    if types == {"Point"}:
        return MultiPoint(flat)
    if types == {"LineString"}:
        return MultiLineString(flat)
    if types == {"Polygon"}:
        return MultiPolygon(flat)
    return GeometryCollection(flat)


def _properties(placemark: Element) -> dict[str, str | None]:
    props: dict[str, str | None] = {}
    for key in ("name", "description"):
        if (value := _text(_child(placemark, key))) is not None:
            props[key] = value
    extended = _child(placemark, "ExtendedData")
    if extended is not None:
        for data in _children(extended, "Data"):
            if name := data.get("name"):
                props[name] = _text(_child(data, "value"))
        for schema_data in _children(extended, "SchemaData"):
            for simple in _children(schema_data, "SimpleData"):
                if name := simple.get("name"):
                    props[name] = _text(simple)
    return props


def _placemark_to_feature(placemark: Element, layer: str) -> RawFeature:
    props = _properties(placemark)
    source_id = placemark.get("id")
    geom_el = next((c for c in placemark if _local(c.tag) in _SUPPORTED | _UNSUPPORTED), None)
    if geom_el is None:
        return RawFeature(None, props, source_id, layer)

    kind = _local(geom_el.tag)
    if kind in _UNSUPPORTED:
        return RawFeature(
            None, props, source_id, layer, declared_type=kind, unsupported=True,
            error=f"KML <{kind}> geometries are not supported.",
        )  # fmt: skip
    try:
        geom = _parse_geometry(geom_el)
    except GeometryParseError as exc:
        return RawFeature(None, props, source_id, layer, declared_type=kind, error=str(exc))
    except RecursionError:
        return RawFeature(
            None, props, source_id, layer, declared_type=kind, error="MultiGeometry nested too deeply."
        )
    return RawFeature(geom, props, source_id, layer, declared_type=geom.geom_type)


def _walk(root: Element) -> Iterator[RawFeature]:
    """Yield Placemarks in document order. Iterative, so hostile nesting can't blow the stack."""
    stack: list[tuple[Iterator[Element], tuple[str, ...]]] = [(iter(root), ())]
    while stack:
        children, path = stack[-1]
        child = next(children, None)
        if child is None:
            stack.pop()
            continue
        kind = _local(child.tag)
        if kind == "Placemark":
            yield _placemark_to_feature(child, "/".join(path) or "default")
        elif kind in _CONTAINERS:
            name = _text(_child(child, "name"))
            stack.append((iter(child), (*path, name) if name else path))


def read_kml(data: bytes) -> Dataset:
    try:
        root = SafeET.fromstring(data)
    except DefusedXmlException as exc:
        raise InvalidGeoFile("KML uses forbidden XML constructs (entities/DTD).") from exc
    except SafeET.ParseError as exc:
        raise InvalidGeoFile(f"KML is not well-formed XML: {exc}") from exc

    if _local(root.tag) != "kml":
        raise InvalidGeoFile(f"Root element is <{_local(root.tag)}>, expected <kml>.")

    # KML is single-CRS, so the whole document is one stream. Each feature keeps its
    # Folder path as its layer name.
    return Dataset(file_format="kml", layers=[Layer(name="kml", crs=WGS84, features=_walk(root))])


def read_kmz(data: bytes, limits: ArchiveLimits) -> Dataset:
    archive = SafeZip(data, limits)
    kml_members = [n for n in archive.members if n.lower().endswith(".kml")]
    if not kml_members:
        raise InvalidGeoFile("KMZ archive does not contain a .kml document.")
    # The KMZ spec says the first .kml at the root is the main document (usually doc.kml).
    root_level = [n for n in kml_members if len(PurePosixPath(n).parts) == 1]
    main = (root_level or kml_members)[0]
    dataset = read_kml(archive.read(main))
    dataset.file_format = "kmz"
    return dataset
