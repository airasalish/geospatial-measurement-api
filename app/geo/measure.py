"""Per-feature measurement, computed in vectorised batches.

| Geometry                         | Measured                           |
|----------------------------------|------------------------------------|
| Polygon, MultiPolygon            | area_m2, perimeter_m (incl. holes) |
| LineString, MultiLineString      | length_m                           |
| Point, MultiPoint                | nothing (NOT_APPLICABLE)           |
| GeometryCollection               | area of polygon parts, length of line parts |

Nothing here raises for bad input. Every outcome, including failures, comes back
as a ``Measurement`` so one bad feature never fails the whole file.

The batch pipeline:
  1. classify each feature (cheap Python): settle points, empties, unsupported types,
     unknown CRSs, and split the rest into polygonal and lineal parts;
  2. for each part kind, as numpy arrays: reproject to WGS84 -> repair invalid
     polygons -> densify long edges -> project per centre group -> area/length.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
import shapely
from pyproj import CRS
from pyproj.exceptions import ProjError
from shapely.geometry import GeometryCollection, MultiLineString, MultiPolygon
from shapely.geometry.base import BaseGeometry

from app.geo import crs as crs_utils
from app.geo.types import RawFeature
from app.models import MeasurementStatus

_POLYGONAL = ("Polygon", "MultiPolygon")
_LINEAL = ("LineString", "MultiLineString", "LinearRing")
_PUNTAL = ("Point", "MultiPoint")
# Millimetre precision. Survey-grade input rarely beats a centimetre, so more
# digits would be false precision.
_DECIMALS = 3


@dataclass
class Measurement:
    status: MeasurementStatus
    area_m2: float | None = None
    perimeter_m: float | None = None
    length_m: float | None = None
    crs: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def note(self) -> str | None:
        return " ".join(self.notes) or None


def _split(geom: BaseGeometry) -> tuple[BaseGeometry | None, BaseGeometry | None]:
    """Split a geometry into (polygonal part, lineal part). Points are dropped."""
    if geom.geom_type in _POLYGONAL:
        return geom, None
    if geom.geom_type in _LINEAL:
        return None, geom
    if isinstance(geom, GeometryCollection):
        parts = list(shapely.get_parts(geom))
        polys = [p for g in parts if g.geom_type in _POLYGONAL for p in shapely.get_parts(g)]
        lines = [ln for g in parts if g.geom_type in _LINEAL for ln in shapely.get_parts(g)]
        return (MultiPolygon(polys) if polys else None), (MultiLineString(lines) if lines else None)
    return None, None


def _repair(poly: BaseGeometry, notes: list[str]) -> BaseGeometry:
    """Self-intersecting polygons give wrong areas (a bow-tie's lobes cancel out). Repair them."""
    fixed = shapely.make_valid(poly)
    notes.append(f"Invalid polygon ({shapely.is_valid_reason(poly)}) was repaired before measuring.")
    polys = [p for p in shapely.get_parts(fixed) if p.geom_type in _POLYGONAL]
    return MultiPolygon([q for p in polys for q in shapely.get_parts(p)])


def _classify(
    feature: RawFeature, source_crs: CRS | None
) -> Measurement | tuple[Measurement, BaseGeometry | None, BaseGeometry | None]:
    """Settle trivial cases now. Otherwise return (pending result, polygonal, lineal)."""
    geom = feature.geometry
    if geom is None:
        if feature.unsupported:
            return Measurement(MeasurementStatus.UNSUPPORTED, notes=[feature.error or ""])
        if feature.error:  # the source declared a geometry we couldn't read
            return Measurement(MeasurementStatus.FAILED, notes=[feature.error])
        # A legitimately empty record (shapefile NULL shape, Placemark without geometry).
        return Measurement(MeasurementStatus.NOT_APPLICABLE, notes=["Feature has no geometry."])
    if geom.is_empty:
        return Measurement(MeasurementStatus.FAILED, notes=["Geometry is empty."])
    if geom.geom_type in _PUNTAL:
        return Measurement(MeasurementStatus.NOT_APPLICABLE)

    polygonal, lineal = _split(geom)
    if polygonal is None and lineal is None:
        if geom.geom_type == "GeometryCollection":
            return Measurement(MeasurementStatus.NOT_APPLICABLE, notes=["Collection contains only points."])
        return Measurement(MeasurementStatus.UNSUPPORTED, notes=[f"{geom.geom_type} is not measurable."])

    pending = Measurement(MeasurementStatus.MEASURED)
    if source_crs is None:
        if not crs_utils.looks_geographic(geom):
            return Measurement(
                MeasurementStatus.FAILED,
                notes=["CRS is unknown and coordinates are not lon/lat; cannot measure safely."],
            )
        pending.notes.append("CRS unknown; coordinates look like lon/lat so EPSG:4326 was assumed.")
    return pending, polygonal, lineal


def _measure_polygons(geoms: list[BaseGeometry], results: list[Measurement], crs: CRS) -> None:
    wgs = crs_utils.to_wgs84(np.asarray(geoms, dtype=object), crs)
    for j in np.flatnonzero(~shapely.is_valid(wgs)):
        wgs[j] = _repair(wgs[j], results[j].notes)
    wgs = crs_utils.densify_long_segments(wgs)

    area_geoms, area_crs = crs_utils.project_local(wgs, "laea")
    perim_geoms, perim_crs = crs_utils.project_local(wgs, "aeqd")
    areas, perimeters = shapely.area(area_geoms), shapely.length(perim_geoms)
    for i, r in enumerate(results):
        r.area_m2 = round(float(areas[i]), _DECIMALS)
        r.perimeter_m = round(float(perimeters[i]), _DECIMALS)
        r.crs.update(area=area_crs[i], perimeter=perim_crs[i])


def _measure_lines(geoms: list[BaseGeometry], results: list[Measurement], crs: CRS) -> None:
    wgs = crs_utils.densify_long_segments(crs_utils.to_wgs84(np.asarray(geoms, dtype=object), crs))
    projected, line_crs = crs_utils.project_local(wgs, "aeqd")
    lengths = shapely.length(projected)
    for i, r in enumerate(results):
        r.length_m = round(float(lengths[i]), _DECIMALS)
        r.crs["length"] = line_crs[i]


def _finalise(m: Measurement) -> Measurement:
    values = (m.area_m2, m.perimeter_m, m.length_m)
    if all(v is None or math.isfinite(v) for v in values):
        return m
    return Measurement(
        MeasurementStatus.FAILED,
        notes=["Coordinates fall outside the valid area of the CRS (reprojection returned infinity)."],
    )


def measure_batch(features: Sequence[RawFeature], source_crs: CRS | None) -> list[Measurement]:
    """Measure features that share a source CRS. Returns one Measurement per feature, in order."""
    results: list[Measurement] = []
    polys: list[tuple[int, BaseGeometry]] = []
    lines: list[tuple[int, BaseGeometry]] = []

    for i, feature in enumerate(features):
        outcome = _classify(feature, source_crs)
        if isinstance(outcome, Measurement):
            results.append(outcome)
            continue
        pending, polygonal, lineal = outcome
        results.append(pending)
        if polygonal is not None:
            polys.append((i, polygonal))
        if lineal is not None:
            lines.append((i, lineal))

    # With an unknown CRS, every feature still pending was checked to be lon/lat.
    crs = source_crs or crs_utils.WGS84
    try:
        if polys:
            _measure_polygons([g for _, g in polys], [results[i] for i, _ in polys], crs)
        if lines:
            _measure_lines([g for _, g in lines], [results[i] for i, _ in lines], crs)
    except ProjError as exc:
        if len(features) == 1:
            return [Measurement(MeasurementStatus.FAILED, notes=[f"Reprojection failed: {exc}"])]
        # Find the culprit(s): measure one by one so the other features still succeed.
        return [measure_batch([f], source_crs)[0] for f in features]

    return [_finalise(m) for m in results]


def measure(feature: RawFeature, source_crs: CRS | None) -> Measurement:
    return measure_batch([feature], source_crs)[0]
