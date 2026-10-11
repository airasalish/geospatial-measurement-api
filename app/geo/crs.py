"""CRS helpers: describing source CRSs and projecting geometries for measurement.

Strategy (see README "CRS handling"):

* Every geometry is first normalised to WGS84 lon/lat (EPSG:4326). This handles
  geographic CRSs on other datums (NAD83, ETRS89) and projections that can't be
  measured in (Web Mercator) with one code path.
* Segments longer than 1 km are densified along the geodesic, so that edges stay
  "shortest path on the ellipsoid" whatever projection comes next.
* Each geometry is then projected into a projection centred on that feature:
    - Lambert Azimuthal Equal-Area (LAEA) for areas. It is equal-area by
      construction, so area is preserved no matter where the feature is.
    - Azimuthal Equidistant (AEQD) for lengths. Scale error grows with the square of
      the distance from the centre: under 0.01% for features up to ~150 km across.
  Unlike UTM, this works across zone boundaries and at the poles, and it isn't
  limited by UTM's 0.04-0.1% scale error.

Performance: every function here works on numpy arrays of geometries. Projection
centres are snapped to a 0.1 degree grid, features are grouped by centre, and each
group is projected in one vectorised pyproj call. Snapping has no effect on LAEA
areas, and it shifts the AEQD centre by at most ~8 km, which adds an error below 1e-6.
"""

from functools import lru_cache

import numpy as np
import numpy.typing as npt
import shapely
from pyproj import CRS, Geod, Transformer
from shapely.geometry import GeometryCollection, LineString, MultiLineString, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

GeometryArray = npt.NDArray[np.object_]

WGS84 = CRS.from_epsg(4326)
GEOD = Geod(ellps="WGS84")
CENTER_GRID_DEG = 0.1
DENSIFY_MAX_SEGMENT_M = 1_000.0
_MAX_POINTS_PER_SEGMENT = 1_000


def describe_crs(crs: CRS | None) -> str | None:
    """Short label for display, e.g. 'EPSG:4326'. Falls back to the CRS name."""
    if crs is None:
        return None
    epsg = crs.to_epsg(min_confidence=70)
    if epsg is not None:
        return f"EPSG:{epsg}"
    return (crs.name or "custom")[:64]


def looks_geographic(geom: BaseGeometry) -> bool:
    """True if every coordinate is a plausible lon/lat. Used to guess a missing CRS."""
    minx, miny, maxx, maxy = geom.bounds
    return bool(-180 <= minx <= maxx <= 180 and -90 <= miny <= maxy <= 90)


# --------------------------------------------------------------------- transforms


def _transform(geoms: GeometryArray, transformer: Transformer) -> GeometryArray:
    """Transform every coordinate of every geometry in a single pyproj call (drops Z)."""

    def fn(xy: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
        x, y = transformer.transform(xy[:, 0], xy[:, 1])
        return np.column_stack([x, y])

    result: GeometryArray = shapely.transform(geoms, fn)
    return result


@lru_cache(maxsize=64)
def _to_wgs84_transformer(crs_wkt: str) -> Transformer:
    return Transformer.from_crs(CRS.from_wkt(crs_wkt), WGS84, always_xy=True)


def to_wgs84(geoms: GeometryArray, crs: CRS) -> GeometryArray:
    if crs == WGS84:
        result: GeometryArray = shapely.force_2d(geoms)
        return result
    return _transform(geoms, _to_wgs84_transformer(crs.to_wkt()))


# ------------------------------------------------------------------ densification


def _densify_coords(xy: npt.NDArray[np.float64], max_segment_m: float) -> npt.NDArray[np.float64]:
    if len(xy) < 2:
        return xy
    _, _, dist = GEOD.inv(xy[:-1, 0], xy[:-1, 1], xy[1:, 0], xy[1:, 1])
    out: list[tuple[float, float]] = [(xy[0, 0], xy[0, 1])]
    for (x0, y0), (x1, y1), d in zip(xy[:-1], xy[1:], np.atleast_1d(dist), strict=True):
        n = min(int(d // max_segment_m), _MAX_POINTS_PER_SEGMENT)
        if n:
            out.extend(GEOD.npts(x0, y0, x1, y1, n))
        out.append((x1, y1))
    return np.asarray(out)


def densify_geodesic(geom: BaseGeometry, max_segment_m: float = DENSIFY_MAX_SEGMENT_M) -> BaseGeometry:
    """Insert vertices along the geodesic of any segment longer than ``max_segment_m``.

    Between two lon/lat vertices, the "true" edge is the geodesic: the shortest path
    on the ellipsoid. After projection, edges become straight lines in the plane,
    and those drift from the geodesic as segments get longer. For a 1 degree square
    the area error is ~1e-4. Densifying first makes the projected edges follow the
    geodesic, so results match ellipsoidal ground truth whatever the feature size.
    """

    def ring(r: BaseGeometry) -> npt.NDArray[np.float64]:
        return _densify_coords(np.asarray(r.coords)[:, :2], max_segment_m)

    if isinstance(geom, Polygon):
        return Polygon(ring(geom.exterior), [ring(h) for h in geom.interiors])
    if isinstance(geom, LineString):  # includes LinearRing
        return LineString(ring(geom))
    if isinstance(geom, (MultiPolygon, MultiLineString, GeometryCollection)):
        return type(geom)([densify_geodesic(g, max_segment_m) for g in geom.geoms])
    return geom


def densify_long_segments(
    geoms: GeometryArray, max_segment_m: float = DENSIFY_MAX_SEGMENT_M
) -> GeometryArray:
    """Densify only the geometries that have a segment longer than ``max_segment_m``.

    Segment lengths for the whole batch come from one vectorised geodesic call.
    Survey-scale data rarely has segments over 1 km, so usually nothing is rebuilt.
    """
    coords, owner = shapely.get_coordinates(geoms, return_index=True)
    if len(coords) < 2:
        return geoms
    _, _, dist = GEOD.inv(coords[:-1, 0], coords[:-1, 1], coords[1:, 0], coords[1:, 1])
    # Pairs that span two geometries aren't segments. Pairs that jump between rings
    # of one polygon are, harmlessly, counted: at worst a polygon gets densified needlessly.
    dist = np.where(owner[1:] == owner[:-1], dist, 0.0)
    longest = np.zeros(len(geoms))
    np.maximum.at(longest, owner[1:], dist)

    needs = np.flatnonzero(longest > max_segment_m)
    if needs.size == 0:
        return geoms
    out = geoms.copy()
    for i in needs:
        out[i] = densify_geodesic(geoms[i], max_segment_m)
    return out


# --------------------------------------------------------------- local projection


@lru_cache(maxsize=4096)
def _local_transformer(proj: str, lon0: float, lat0: float) -> tuple[Transformer, str]:
    definition = f"+proj={proj} +lat_0={lat0} +lon_0={lon0} +datum=WGS84 +units=m +no_defs"
    return Transformer.from_crs(WGS84, CRS.from_proj4(definition), always_xy=True), definition


def _centers(geoms: GeometryArray) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    centroids = shapely.centroid(geoms)
    lon, lat = shapely.get_x(centroids), shapely.get_y(centroids)
    # Degenerate geometries (e.g. zero-area polygons) have an empty centroid; use the bbox centre.
    bad = np.isnan(lon) | np.isnan(lat)
    if bad.any():
        b = shapely.bounds(geoms[bad])
        lon[bad], lat[bad] = (b[:, 0] + b[:, 2]) / 2, (b[:, 1] + b[:, 3]) / 2
    return lon, lat


def project_local(geoms: GeometryArray, proj: str) -> tuple[GeometryArray, list[str]]:
    """Project WGS84 geometries into ``proj`` ('laea' | 'aeqd') CRSs centred on each one.

    Returns the projected geometries (units: metres) and the PROJ definition used for each.
    """
    lon, lat = _centers(geoms)
    snapped = np.column_stack([lon, lat]) / CENTER_GRID_DEG
    snapped = np.round(np.round(snapped) * CENTER_GRID_DEG, 6)
    centres, group = np.unique(snapped, axis=0, return_inverse=True)
    group = group.ravel()
    # Indices of each group's members in one O(n log n) pass.
    order = np.argsort(group, kind="stable")
    members_by_group = np.split(order, np.cumsum(np.bincount(group))[:-1])

    projected: GeometryArray = np.empty(len(geoms), dtype=object)
    definitions: list[str] = [""] * len(geoms)
    for (lon0, lat0), members in zip(centres, members_by_group, strict=True):
        transformer, definition = _local_transformer(proj, float(lon0), float(lat0))
        projected[members] = _transform(geoms[members], transformer)
        for i in members:
            definitions[i] = definition
    return projected, definitions
