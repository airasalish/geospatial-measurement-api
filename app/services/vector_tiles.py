"""Mapbox Vector Tiles (MVT) for a file's features.

GeoJSON for a whole file is fine up to tens of thousands of features. Beyond that
a map should only fetch what is on screen, clipped and quantised per tile.

* PostgreSQL/PostGIS: ``ST_AsMVT``/``ST_AsMVTGeom`` over the GiST-indexed ``geom``
  column. The database does the clipping and encoding.
* SQLite: the same tile built in Python. The ``minx..maxy`` columns prefilter,
  shapely clips, and ``mapbox-vector-tile`` encodes.

Both produce the same ``features`` layer and attributes, so a client can't tell
which backend served a tile.
"""

from typing import Any

import mapbox_vector_tile
import numpy as np
import shapely
from morecantile import Tile, tms
from pyproj import Transformer
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import Feature

EXTENT = 4096
BUFFER = 64  # in tile units, so lines and polygon edges don't show seams
LAYER = "features"
WEB_MERCATOR = tms.get("WebMercatorQuad")
_TO_3857 = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)

_POSTGIS_SQL = text(
    """
    WITH bounds AS (SELECT ST_TileEnvelope(:z, :x, :y) AS geom)
    SELECT ST_AsMVT(t, 'features', :extent, 'geom') FROM (
        SELECT f.index, f.geometry_type, f.measurement_status AS status,
               f.area_m2, f.perimeter_m, f.length_m, f.layer,
               COALESCE(f.properties->>'name', f.properties->>'Name', f.properties->>'NAME',
                        f.properties->>'title', f.properties->>'label') AS name,
               ST_AsMVTGeom(ST_Transform(f.geom, 3857), bounds.geom, :extent, :buffer, true) AS geom
        FROM features f, bounds
        WHERE f.file_id = :file_id
          AND f.geom && ST_Transform(bounds.geom, 4326)
    ) t
    WHERE t.geom IS NOT NULL
    """
)


_NAME_KEYS = ("name", "Name", "NAME", "title", "label")


def _name(properties: dict[str, Any] | None) -> str | None:
    """Same rule as the PostGIS query above, so both backends label features identically."""
    for key in _NAME_KEYS:
        value = (properties or {}).get(key)
        if value is not None:
            return str(value)
    return None


def _props(row: Any) -> dict[str, Any]:
    props = {
        "index": row.index,
        "geometry_type": row.geometry_type,
        "status": str(row.measurement_status),
        "area_m2": row.area_m2,
        "perimeter_m": row.perimeter_m,
        "length_m": row.length_m,
        "layer": row.layer,
        "name": _name(row.properties),
    }
    return {k: v for k, v in props.items() if v is not None}  # MVT has no null values


def _python_tile(session: Session, file_id: str, z: int, x: int, y: int) -> bytes:
    b = WEB_MERCATOR.bounds(Tile(x, y, z))
    pad_deg = (b.right - b.left) * BUFFER / EXTENT
    rows = session.execute(
        select(
            Feature.index,
            Feature.geometry_type,
            Feature.measurement_status,
            Feature.area_m2,
            Feature.perimeter_m,
            Feature.length_m,
            Feature.layer,
            Feature.properties,
            Feature.geometry_wgs84,
        )
        .where(
            Feature.file_id == file_id,
            Feature.maxx >= b.left - pad_deg,
            Feature.minx <= b.right + pad_deg,
            Feature.maxy >= b.bottom - pad_deg,
            Feature.miny <= b.top + pad_deg,
        )
        .order_by(Feature.index)
    ).all()
    if not rows:
        return b""
    xb = WEB_MERCATOR.xy_bounds(Tile(x, y, z))
    pad = (xb.right - xb.left) * BUFFER / EXTENT
    geoms = shapely.from_geojson(np.array([r.geometry_wgs84 for r in rows], dtype=object))

    def fn(c: np.ndarray) -> np.ndarray:
        px, py = _TO_3857.transform(c[:, 0], c[:, 1])
        return np.column_stack([px, py])

    merc = shapely.transform(geoms, fn)
    clipped = shapely.clip_by_rect(merc, xb.left - pad, xb.bottom - pad, xb.right + pad, xb.top + pad)
    features = [
        {"geometry": g, "properties": _props(r)}
        for r, g in zip(rows, clipped, strict=True)
        if g is not None and not g.is_empty
    ]
    if not features:
        return b""
    tile: bytes = mapbox_vector_tile.encode(
        [{"name": LAYER, "features": features}],
        default_options={
            "quantize_bounds": (xb.left, xb.bottom, xb.right, xb.top),
            "extents": EXTENT,
            "y_coord_down": False,
        },
    )
    return tile


def file_tile(session: Session, file_id: str, z: int, x: int, y: int) -> bytes:
    """Encoded MVT for one tile (empty bytes when nothing intersects it)."""
    bind = session.get_bind()
    if bind.dialect.name == "postgresql":
        params = {"z": z, "x": x, "y": y, "extent": EXTENT, "buffer": BUFFER, "file_id": file_id}
        data = session.execute(_POSTGIS_SQL, params).scalar()
        return bytes(data or b"")
    return _python_tile(session, file_id, z, x, y)


def populate_postgis_geometry(session: Session, file_id: str) -> None:
    """Fill the native PostGIS column from the stored WGS84 GeoJSON (one set-based UPDATE)."""
    if session.get_bind().dialect.name != "postgresql":
        return
    session.execute(
        text(
            "UPDATE features SET geom = ST_SetSRID(ST_GeomFromGeoJSON(geometry_wgs84), 4326) "
            "WHERE file_id = :file_id AND geometry_wgs84 IS NOT NULL"
        ),
        {"file_id": file_id},
    )
