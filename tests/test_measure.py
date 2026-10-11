"""Measurement correctness.

Ground truth is pyproj.Geod, which computes geodesic area and length on the WGS84
ellipsoid (Karney's algorithms). Our projected measurements have to agree with it.
"""

import numpy as np
import pytest
import shapely
from pyproj import CRS, Geod, Transformer
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
    box,
)

from app.geo.measure import measure
from app.geo.types import RawFeature
from app.models import MeasurementStatus

GEOD = Geod(ellps="WGS84")
WGS84 = CRS.from_epsg(4326)


def feat(geom, **kw) -> RawFeature:
    return RawFeature(geom, {}, **kw)


def geodesic_area(poly) -> float:
    return abs(GEOD.geometry_area_perimeter(poly)[0])


def rel_err(a: float, b: float) -> float:
    return abs(a - b) / b


@pytest.mark.parametrize(
    ("lon", "lat", "size"),
    [
        (77.59, 12.97, 0.01),  # Bengaluru, ~1 km2
        (0.0, 0.0, 0.5),  # equator, ~3,000 km2
        (10.0, 60.0, 1.0),  # Norway, high latitude
        (-70.0, -33.0, 0.2),  # southern hemisphere
        (-0.5, 51.3, 1.0),  # straddles the prime meridian
        (5.5, 45.0, 1.0),  # straddles a UTM zone boundary (6 deg E)
        (30.0, 85.0, 2.0),  # beyond UTM's coverage (>84 deg N)
    ],
)
def test_polygon_area_matches_geodesic(lon, lat, size):
    poly = box(lon, lat, lon + size, lat + size)
    m = measure(feat(poly), WGS84)
    assert m.status == MeasurementStatus.MEASURED
    # LAEA is equal-area, so agreement should be near float precision.
    assert rel_err(m.area_m2, geodesic_area(poly)) < 1e-6
    assert m.perimeter_m == pytest.approx(GEOD.geometry_length(poly.exterior), rel=1e-3)
    assert m.length_m is None


@pytest.mark.parametrize(
    "coords",
    [
        [(77.59, 12.97), (77.60, 12.98), (77.62, 12.97)],  # ~4 km
        [(-74.0, 40.7), (-73.5, 41.2)],  # ~70 km
        [(10.0, 60.0), (11.0, 60.5), (12.0, 60.0)],  # ~120 km, high latitude
    ],
)
def test_line_length_matches_geodesic(coords):
    line = LineString(coords)
    m = measure(feat(line), WGS84)
    assert m.status == MeasurementStatus.MEASURED
    assert rel_err(m.length_m, GEOD.geometry_length(line)) < 1e-4
    assert m.area_m2 is None


def test_measuring_in_degrees_would_be_wrong():
    """Sanity check: a 0.01 x 0.01 degree square is ~1.2 km2, not 0.0001."""
    m = measure(feat(box(77.59, 12.97, 77.60, 12.98)), WGS84)
    assert m.area_m2 == pytest.approx(1_200_290, rel=1e-5)


def test_polygon_with_hole_subtracts_hole():
    outer = box(0, 0, 0.1, 0.1)
    hole = box(0.025, 0.025, 0.075, 0.075)
    poly = Polygon(outer.exterior.coords, [hole.exterior.coords])
    m = measure(feat(poly), WGS84)
    assert m.area_m2 == pytest.approx(geodesic_area(outer) - geodesic_area(hole), rel=1e-6)
    # Perimeter includes the interior ring.
    assert m.perimeter_m == pytest.approx(
        GEOD.geometry_length(outer.exterior) + GEOD.geometry_length(hole.exterior), rel=1e-3
    )


def test_multipolygon_sums_parts():
    a, b = box(0, 0, 0.1, 0.1), box(1, 1, 1.1, 1.1)
    m = measure(feat(MultiPolygon([a, b])), WGS84)
    assert m.area_m2 == pytest.approx(geodesic_area(a) + geodesic_area(b), rel=1e-6)


def test_projected_source_crs_is_handled():
    """A 1 km square defined in UTM 43N (EPSG:32643) should measure ~1 km2."""
    utm = CRS.from_epsg(32643)
    poly = box(500_000, 1_430_000, 501_000, 1_431_000)
    m = measure(feat(poly), utm)
    to_wgs = Transformer.from_crs(utm, WGS84, always_xy=True).transform
    poly_wgs = shapely.transform(poly, lambda c: np.column_stack(to_wgs(c[:, 0], c[:, 1])))
    assert rel_err(m.area_m2, geodesic_area(poly_wgs)) < 1e-6
    # And it is close to the nominal 1 km2 (UTM scale factor is ~0.9996 at the central meridian).
    assert m.area_m2 == pytest.approx(1_000_000, rel=1e-3)


def test_web_mercator_is_not_measured_naively():
    """Web Mercator inflates area by 1/cos^2(lat): a 1 km 'square' at 60N is really ~0.25 km2."""
    merc = CRS.from_epsg(3857)
    to_merc = Transformer.from_crs(WGS84, merc, always_xy=True).transform
    x, y = to_merc(10.0, 60.0)
    poly = box(x, y, x + 1000, y + 1000)
    m = measure(feat(poly), merc)
    assert m.area_m2 == pytest.approx(250_000, rel=0.01)


def test_non_wgs84_geographic_crs():
    nad83 = CRS.from_epsg(4269)
    poly = box(-100.0, 40.0, -99.9, 40.1)
    m = measure(feat(poly), nad83)
    assert m.area_m2 == pytest.approx(geodesic_area(poly), rel=1e-5)


def test_3d_coordinates_are_measured_in_2d():
    poly = Polygon([(0, 0, 100), (0, 0.1, 500), (0.1, 0.1, 900), (0.1, 0, 100)])
    m = measure(feat(poly), WGS84)
    assert m.area_m2 == pytest.approx(geodesic_area(box(0, 0, 0.1, 0.1)), rel=1e-6)


def test_self_intersecting_polygon_is_repaired():
    # A bow-tie. The shoelace formula would give ~0 because the two lobes cancel.
    bowtie = Polygon([(0, 0), (0.1, 0.1), (0.1, 0), (0, 0.1), (0, 0)])
    m = measure(feat(bowtie), WGS84)
    assert m.status == MeasurementStatus.MEASURED
    expected = geodesic_area(box(0, 0, 0.1, 0.1)) / 2
    assert m.area_m2 == pytest.approx(expected, rel=1e-3)
    assert "repaired" in m.note


@pytest.mark.parametrize("geom", [Point(1, 1), MultiPoint([(1, 1), (2, 2)])])
def test_points_are_not_applicable(geom):
    m = measure(feat(geom), WGS84)
    assert m.status == MeasurementStatus.NOT_APPLICABLE
    assert (m.area_m2, m.length_m, m.perimeter_m) == (None, None, None)


def test_geometry_collection_measures_each_dimension():
    poly, line = box(0, 0, 0.1, 0.1), LineString([(1, 1), (1.1, 1.1)])
    m = measure(feat(GeometryCollection([poly, line, Point(5, 5)])), WGS84)
    assert m.status == MeasurementStatus.MEASURED
    assert m.area_m2 == pytest.approx(geodesic_area(poly), rel=1e-6)
    assert m.length_m == pytest.approx(GEOD.geometry_length(line), rel=1e-4)


def test_points_only_collection_is_not_applicable():
    m = measure(feat(GeometryCollection([Point(0, 0)])), WGS84)
    assert m.status == MeasurementStatus.NOT_APPLICABLE


def test_empty_geometry_fails_gracefully():
    m = measure(feat(Polygon()), WGS84)
    assert m.status == MeasurementStatus.FAILED
    assert "empty" in m.note.lower()


def test_null_geometry_is_not_applicable():
    m = measure(feat(None), WGS84)
    assert m.status == MeasurementStatus.NOT_APPLICABLE
    assert "no geometry" in m.note


def test_malformed_geometry_fails_with_reason():
    m = measure(feat(None, error="Invalid coordinate 'abc,1'."), WGS84)
    assert m.status == MeasurementStatus.FAILED
    assert "Invalid coordinate" in m.note


def test_unsupported_geometry_is_reported():
    m = measure(feat(None, unsupported=True, error="KML <Model> geometries are not supported."), WGS84)
    assert m.status == MeasurementStatus.UNSUPPORTED
    assert "Model" in m.note


def test_unknown_crs_with_lonlat_coordinates_assumes_wgs84():
    m = measure(feat(box(77.59, 12.97, 77.60, 12.98)), None)
    assert m.status == MeasurementStatus.MEASURED
    assert "EPSG:4326 was assumed" in m.note


def test_unknown_crs_with_projected_coordinates_refuses_to_guess():
    m = measure(feat(box(500_000, 1_430_000, 501_000, 1_431_000)), None)
    assert m.status == MeasurementStatus.FAILED
    assert m.area_m2 is None


def test_measurement_reports_projection_used():
    m = measure(feat(box(77.59, 12.97, 77.60, 12.98)), WGS84)
    assert m.crs["area"].startswith("+proj=laea")
    assert m.crs["perimeter"].startswith("+proj=aeqd")
    assert "+lat_0=13.0" in m.crs["area"]
