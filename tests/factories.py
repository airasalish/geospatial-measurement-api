"""Builders for test inputs, so the repo has no opaque binary fixtures."""

import datetime as dt
import io
import zipfile
from collections.abc import Sequence
from typing import Any

import shapefile
from pyproj import CRS

KML_NS = "http://www.opengis.net/kml/2.2"


def prj(epsg: int) -> str:
    return CRS.from_epsg(epsg).to_wkt("WKT1_ESRI")


def shapefile_parts(
    shape_type: int,
    geometries: Sequence[Any],
    records: Sequence[dict[str, Any]] | None = None,
    fields: Sequence[tuple[str, str]] = (("name", "C"),),
    encoding: str = "utf-8",
) -> dict[str, bytes]:
    """Build in-memory .shp/.shx/.dbf. A ``None`` geometry writes a NULL shape."""
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    w = shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shape_type, encoding=encoding)
    for name, kind in fields:
        if kind == "N":
            w.field(name, "N", decimal=3)
        elif kind == "D":
            w.field(name, "D")
        else:
            w.field(name, kind)
    records = records or [{"name": f"f{i}"} for i in range(len(geometries))]
    for geom, rec in zip(geometries, records, strict=True):
        if geom is None:
            w.null()
        elif shape_type in (shapefile.POLYGON, shapefile.POLYGONZ):
            w.poly(geom)
        elif shape_type in (shapefile.POLYLINE, shapefile.POLYLINEZ):
            w.line(geom)
        elif shape_type == shapefile.POINT:
            w.point(*geom)
        elif shape_type == shapefile.MULTIPOINT:
            w.multipoint(geom)
        w.record(*[rec.get(n) for n, _ in fields])
    w.close()
    return {".shp": shp.getvalue(), ".shx": shx.getvalue(), ".dbf": dbf.getvalue()}


def zip_bytes(members: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return buf.getvalue()


def shapefile_zip(
    stem: str = "parcels",
    *,
    epsg: int | None = 4326,
    folder: str = "",
    drop: Sequence[str] = (),
    **kwargs: Any,
) -> bytes:
    parts = shapefile_parts(**kwargs)
    members: dict[str, bytes | str] = {f"{folder}{stem}{ext}": data for ext, data in parts.items()}
    if epsg is not None:
        members[f"{folder}{stem}.prj"] = prj(epsg)
    for ext in drop:
        members.pop(f"{folder}{stem}{ext}", None)
    return zip_bytes(members)


def square(lon: float, lat: float, size_deg: float) -> list[list[tuple[float, float]]]:
    """A clockwise square ring (shapefile outer-ring orientation)."""
    return [[
        (lon, lat), (lon, lat + size_deg), (lon + size_deg, lat + size_deg),
        (lon + size_deg, lat), (lon, lat),
    ]]  # fmt: skip


def kml(body: str, ns: str | None = KML_NS) -> bytes:
    xmlns = f' xmlns="{ns}"' if ns else ""
    return f'<?xml version="1.0" encoding="UTF-8"?><kml{xmlns}><Document>{body}</Document></kml>'.encode()


def placemark(geometry: str, name: str = "pm", pid: str | None = None, extra: str = "") -> str:
    id_attr = f' id="{pid}"' if pid else ""
    return f"<Placemark{id_attr}><name>{name}</name>{extra}{geometry}</Placemark>"


def kml_polygon(
    ring: Sequence[tuple[float, float]], holes: Sequence[Sequence[tuple[float, float]]] = ()
) -> str:
    def coords(r: Sequence[tuple[float, float]]) -> str:
        return " ".join(f"{x},{y},0" for x, y in r)

    inner = "".join(
        f"<innerBoundaryIs><LinearRing><coordinates>{coords(h)}</coordinates></LinearRing></innerBoundaryIs>"
        for h in holes
    )
    return (
        f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{coords(ring)}</coordinates>"
        f"</LinearRing></outerBoundaryIs>{inner}</Polygon>"
    )


def kml_line(coords: Sequence[tuple[float, float]]) -> str:
    return f"<LineString><coordinates>{' '.join(f'{x},{y}' for x, y in coords)}</coordinates></LineString>"


TODAY = dt.date(2024, 1, 15)
