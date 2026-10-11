"""DEM volumetrics, validated against analytic ground truth.

A cone of radius r and height h has volume pi*r^2*h/3. The synthetic DEMs are
built in UTM grid units, so the true ground volume is that divided by k^2, where
k is the UTM scale factor at the site (~1.00055 here, 277 km from the central
meridian). The engine must recover that true volume.
"""

import io
import math
from collections.abc import Iterator
from contextlib import contextmanager

import numpy as np
import pytest
import rasterio
from pyproj import CRS, Proj, Transformer
from rasterio.io import MemoryFile
from shapely.geometry import LineString, Point, box, mapping

from app.terrain.analysis import TerrainError, compute_profile, compute_volume, sample_altitude
from app.terrain.grid import cell_metrics
from tests import factories as f

R, H = 20.0, 10.0
CX, CY = f.DEM_ORIGIN[0] + 50, f.DEM_ORIGIN[1] - 50


def _k2() -> float:
    lon, lat = Transformer.from_crs(f.UTM43, "EPSG:4326", always_xy=True).transform(CX, CY)
    fac = Proj(CRS.from_user_input(f.UTM43)).get_factors(lon, lat)
    return fac.parallel_scale * fac.meridional_scale


TRUE_CONE = math.pi * R**2 * H / 3 / _k2()


@contextmanager
def open_dem(z, **kw) -> Iterator[rasterio.io.DatasetReader]:
    # Keep the MemoryFile referenced while the dataset is open: GDAL reads from its buffer.
    with MemoryFile(f.dem_bytes(z, **kw)) as mf, mf.open() as ds:
        yield ds


@pytest.fixture(scope="module")
def grid():
    return f.dem_grid(n=400, res=0.25)


@pytest.fixture(scope="module")
def stockpile_poly():
    return f.utm_to_wgs84(Point(CX, CY).buffer(25, 256))


def rel(a: float, b: float) -> float:
    return abs(a - b) / b


# ----------------------------------------------------------------- volumes


@pytest.mark.parametrize("method", ["lowest", "highest", "mean", "plane", "triangulated"])
def test_cone_on_flat_ground_every_method(grid, stockpile_poly, method):
    x, y = grid
    with open_dem(100 + f.cone(x, y, CX, CY, R, H), res=0.25) as ds:
        r = compute_volume(ds, stockpile_poly, method)
    assert rel(r.cut_m3, TRUE_CONE) < 0.003
    assert r.fill_m3 == pytest.approx(0, abs=0.5)
    assert r.max_height_above_base_m == pytest.approx(H, abs=0.1)
    assert r.coverage_pct == 100


def test_scale_factor_is_applied(grid, stockpile_poly):
    """Without the UTM scale correction the result would be 0.11% too high."""
    x, y = grid
    with open_dem(100 + f.cone(x, y, CX, CY, R, H), res=0.25) as ds:
        r = compute_volume(ds, stockpile_poly, "plane")
    nominal = math.pi * R**2 * H / 3
    assert r.cut_m3 < nominal * 0.9995
    assert "scale-corrected" in r.cell_method


@pytest.mark.parametrize("method", ["plane", "triangulated"])
def test_cone_on_sloped_ground(grid, stockpile_poly, method):
    x, y = grid
    ground = 100 + 0.1 * (x - f.DEM_ORIGIN[0])  # 10% grade
    with open_dem(ground + f.cone(x, y, CX, CY, R, H), res=0.25) as ds:
        r = compute_volume(ds, stockpile_poly, method)
    assert rel(r.cut_m3, TRUE_CONE) < 0.005
    assert not r.warnings


def test_flat_base_on_slope_overestimates_and_warns(grid, stockpile_poly):
    x, y = grid
    ground = 100 + 0.1 * (x - f.DEM_ORIGIN[0])
    with open_dem(ground + f.cone(x, y, CX, CY, R, H), res=0.25) as ds:
        r = compute_volume(ds, stockpile_poly, "lowest")
    assert r.cut_m3 > 1.5 * TRUE_CONE
    assert "Boundary elevation varies" in r.warnings[0]


def test_pit_is_fill(grid, stockpile_poly):
    x, y = grid
    with open_dem(100 - f.cone(x, y, CX, CY, R, H), res=0.25) as ds:
        r = compute_volume(ds, stockpile_poly, "mean")
    assert rel(r.fill_m3, TRUE_CONE) < 0.003
    assert r.cut_m3 == pytest.approx(0, abs=0.5)
    assert r.net_m3 == pytest.approx(-r.fill_m3)
    assert r.max_depth_below_base_m == pytest.approx(H, abs=0.1)


def test_custom_base(grid, stockpile_poly):
    x, y = grid
    with open_dem(100 + f.cone(x, y, CX, CY, R, H), res=0.25) as ds:
        r = compute_volume(ds, stockpile_poly, "custom", custom_elevation=105.0)
    # Only the top half of the cone (height 5, radius 10) is above 105 m.
    assert rel(r.cut_m3, TRUE_CONE / 8) < 0.01
    assert r.fill_m3 > 0
    with open_dem(100 + f.cone(x, y, CX, CY, R, H), res=0.25) as ds, pytest.raises(TerrainError):
        compute_volume(ds, stockpile_poly, "custom")


def test_surface_compare_is_dem_of_difference(grid, stockpile_poly):
    """Survey-to-survey comparison: the change is exactly the new stockpile."""
    x, y = grid
    _x2, y2 = f.dem_grid(n=200, res=0.5)  # the earlier survey is coarser: exercises resampling
    with (
        open_dem(100 + 0.05 * (y - f.DEM_ORIGIN[1]) + f.cone(x, y, CX, CY, R, H), res=0.25) as after,
        open_dem(100 + 0.05 * (y2 - f.DEM_ORIGIN[1]), res=0.5) as before,
    ):
        r = compute_volume(after, stockpile_poly, "surface", other=before)
    assert rel(r.cut_m3, TRUE_CONE) < 0.003


def test_geographic_dem_matches_projected(stockpile_poly):
    """The same cone, stored in lon/lat degrees, measures the same volume."""
    lon_c, lat_c = Transformer.from_crs(f.UTM43, "EPSG:4326", always_xy=True).transform(CX, CY)
    res = 0.25 / 110_000  # ~0.25 m
    n = 500
    lon0, lat0 = lon_c - n / 2 * res, lat_c + n / 2 * res
    lons = lon0 + res * (np.arange(n) + 0.5)
    lats = lat0 - res * (np.arange(n) + 0.5)
    lon_g, lat_g = np.meshgrid(lons, lats)
    to_utm = Transformer.from_crs("EPSG:4326", f.UTM43, always_xy=True)
    x, y = to_utm.transform(lon_g, lat_g)
    z = 100 + f.cone(x, y, CX, CY, R, H)
    with open_dem(z, res=res, crs="EPSG:4326", origin=(lon0, lat0)) as ds:
        r = compute_volume(ds, stockpile_poly, "plane")
    assert r.cell_method == "geodesic per row"
    assert rel(r.cut_m3, TRUE_CONE) < 0.005


def test_surface_area_and_slope_on_45_degree_plane(grid, stockpile_poly):
    x, _y = grid
    with open_dem(100 + (x - f.DEM_ORIGIN[0]), res=0.25) as ds:
        r = compute_volume(ds, stockpile_poly, "plane", slope_threshold_deg=30)
    assert r.surface_area_3d_m2 / r.area_2d_m2 == pytest.approx(math.sqrt(2), rel=2e-3)
    assert r.slope["mean_deg"] == pytest.approx(45, abs=0.1)
    assert r.slope["steeper_than_threshold_pct"] == 100
    assert r.cut_m3 == pytest.approx(0, abs=1)


def test_planimetric_area_matches_polygon(grid, stockpile_poly):
    x, _y = grid
    with open_dem(100 + 0 * x, res=0.25) as ds:
        r = compute_volume(ds, stockpile_poly, "mean")
    true_area = math.pi * 25**2 / _k2()
    assert rel(r.area_2d_m2, true_area) < 0.005


def test_nodata_inside_polygon_is_reported(grid, stockpile_poly):
    x, y = grid
    z = 100 + f.cone(x, y, CX, CY, R, H)
    z[(np.abs(x - CX) < 5) & (np.abs(y - CY) < 5)] = -9999  # a hole at the summit
    with open_dem(z, res=0.25) as ds:
        r = compute_volume(ds, stockpile_poly, "plane")
    assert r.cells["nodata"] > 0
    assert r.coverage_pct < 100
    assert "no elevation" in r.warnings[0]


def test_polygon_outside_dem(grid):
    x, _y = grid
    far = f.utm_to_wgs84(box(CX + 5_000, CY, CX + 5_100, CY + 100))
    with open_dem(100 + 0 * x, res=0.25) as ds, pytest.raises(TerrainError, match="overlap"):
        compute_volume(ds, far, "plane")


def test_volume_rejects_lines(grid):
    x, _y = grid
    line = f.utm_to_wgs84(LineString([(CX, CY), (CX + 10, CY)]))
    with open_dem(100 + 0 * x, res=0.25) as ds, pytest.raises(TerrainError, match="Polygon"):
        compute_volume(ds, line, "plane")


# ---------------------------------------------------------------- profiles


def test_profile_reports_haul_road_grade(grid):
    x, _y = grid
    with open_dem(100 + 0.08 * (x - f.DEM_ORIGIN[0]), res=0.25) as ds:  # 8% grade
        line = f.utm_to_wgs84(LineString([(f.DEM_ORIGIN[0] + 5, CY), (f.DEM_ORIGIN[0] + 95, CY)]))
        p = compute_profile(ds, line)
    assert p.length_m == pytest.approx(90 / math.sqrt(_k2()), rel=1e-3)
    assert p.stats["mean_abs_grade_pct"] == pytest.approx(8, abs=0.05)
    assert p.stats["max_sustained_grade_pct"] == pytest.approx(8, abs=0.05)
    assert p.stats["total_climb_m"] == pytest.approx(7.2, abs=0.05)
    assert p.stats["total_descent_m"] == pytest.approx(0, abs=0.01)
    assert p.distance_m[0] == 0
    assert len(p.distance_m) == len(p.elevation_m)


def test_profile_needs_a_line(grid, stockpile_poly):
    x, _y = grid
    with open_dem(100 + 0 * x, res=0.25) as ds, pytest.raises(TerrainError, match="LineString"):
        compute_profile(ds, stockpile_poly)


def test_altitude(grid):
    x, y = grid
    with open_dem(100 + f.cone(x, y, CX, CY, R, H), res=0.25) as ds:
        lon, lat = Transformer.from_crs(f.UTM43, "EPSG:4326", always_xy=True).transform(CX, CY)
        assert sample_altitude(ds, lon, lat) == pytest.approx(110, abs=0.2)
        assert sample_altitude(ds, lon + 1, lat) is None


def test_geographic_cell_size_at_equator():
    from affine import Affine

    one_arcsec = 1 / 3600
    m = cell_metrics(CRS.from_epsg(4326), Affine(one_arcsec, 0, 0, 0, -one_arcsec, one_arcsec / 2), 1, 1)
    assert m.dx_m[0] == pytest.approx(30.92, abs=0.01)  # 1" of longitude at the equator


# --------------------------------------------------------------------- API


def upload_dem(client, data: bytes, name: str = "site_dem.tif"):
    return client.post("/api/rasters/", files={"file": (name, io.BytesIO(data))})


@pytest.fixture
def dem_id(client, grid):
    x, y = grid
    ground = 100 + 0.1 * (x - f.DEM_ORIGIN[0])
    r = upload_dem(client, f.dem_bytes(ground + f.cone(x, y, CX, CY, R, H), res=0.25))
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_upload_dem_creates_cog(client, dem_id, settings):
    body = client.get(f"/api/rasters/{dem_id}/").json()
    assert body["status"] == "COMPLETED"
    assert body["crs"] == "EPSG:32643"
    assert body["width"] == body["height"] == 400
    assert body["cell_size_m"][0] == pytest.approx(0.25 / 1.00055, abs=1e-4)
    assert body["z_max"] == pytest.approx(115, abs=0.5)
    min_lon, min_lat, max_lon, max_lat = body["bbox"]
    assert 77.5 < min_lon < max_lon < 77.8
    assert 12.9 < min_lat < max_lat < 13.1

    r = client.get(f"/api/rasters/{dem_id}/download/")
    assert r.status_code == 200
    with MemoryFile(r.content) as mf, mf.open() as ds:
        assert ds.tags(ns="IMAGE_STRUCTURE").get("LAYOUT") == "COG"
        assert ds.profile["tiled"]


def test_large_dem_gets_overviews(client):
    """Overviews are what make low-zoom tiles and big-area reads cheap."""
    x, _y = f.dem_grid(n=1200, res=0.5)
    r = upload_dem(client, f.dem_bytes(100 + 0.01 * (x - f.DEM_ORIGIN[0]), res=0.5))
    assert r.status_code == 201, r.text
    dl = client.get(f"/api/rasters/{r.json()['id']}/download/")
    with MemoryFile(dl.content) as mf, mf.open() as ds:
        assert ds.block_shapes[0] == (512, 512)
        assert len(ds.overviews(1)) >= 1


def test_volume_endpoint_inline_geometry(client, dem_id, stockpile_poly):
    r = client.post(
        f"/api/rasters/{dem_id}/volume/",
        json={"geometry": mapping(stockpile_poly), "base": {"method": "plane"}},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert rel(body["cut_m3"], TRUE_CONE) < 0.005
    assert body["base_method"] == "plane"
    assert body["plane"]["dz_dx"] == pytest.approx(0.1, abs=1e-3)


def test_volume_endpoint_feature_reference(client, dem_id, stockpile_poly):
    ring = list(stockpile_poly.exterior.coords)
    kml = f.kml(f.placemark(f.kml_polygon(ring), name="Stockpile A"))
    file_id = client.post("/api/files/", files={"file": ("piles.kml", kml)}).json()["id"]
    r = client.post(f"/api/rasters/{dem_id}/volume/", json={"feature": {"file_id": file_id, "index": 0}})
    assert r.status_code == 200, r.text
    assert rel(r.json()["cut_m3"], TRUE_CONE) < 0.005  # default base: triangulated


def test_batch_volumes_inventory(client, dem_id, stockpile_poly):
    ring = list(stockpile_poly.exterior.coords)
    small = f.utm_to_wgs84(box(CX - 40, CY - 40, CX - 30, CY - 30))  # flat corner, ~0 volume
    body = (
        f.placemark(f.kml_polygon(ring), name="Pile A")
        + f.placemark(f.kml_polygon(list(small.exterior.coords)), name="Pad B")
        + f.placemark("<Point><coordinates>77.7,13.0</coordinates></Point>", name="GCP")
        + f.placemark(f.kml_polygon([(0, 0), (0, 1), (1, 1), (0, 0)]), name="Far away")
    )
    file_id = client.post("/api/files/", files={"file": ("inv.kml", f.kml(body))}).json()["id"]
    r = client.post(f"/api/rasters/{dem_id}/volumes/", json={"file_id": file_id, "base": {"method": "plane"}})
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["measured"], out["skipped"], out["failed"]) == (2, 1, 1)
    statuses = {i["name"]: i["status"] for i in out["items"]}
    assert statuses == {"Pile A": "OK", "Pad B": "OK", "GCP": "SKIPPED", "Far away": "FAILED"}
    assert rel(out["totals"]["cut_m3"], TRUE_CONE) < 0.005


def test_surface_method_via_api(client, grid, stockpile_poly):
    x, y = grid
    ground = 100 + 0.1 * (x - f.DEM_ORIGIN[0])
    before = upload_dem(client, f.dem_bytes(ground, res=0.25), "before.tif").json()["id"]
    after = upload_dem(
        client, f.dem_bytes(ground + f.cone(x, y, CX, CY, R, H), res=0.25), "after.tif"
    ).json()["id"]
    r = client.post(
        f"/api/rasters/{after}/volume/",
        json={"geometry": mapping(stockpile_poly), "base": {"method": "surface", "raster_id": before}},
    )
    assert rel(r.json()["cut_m3"], TRUE_CONE) < 0.003
    r = client.post(
        f"/api/rasters/{after}/volume/",
        json={"geometry": mapping(stockpile_poly), "base": {"method": "surface"}},
    )
    assert r.status_code == 422


def test_geometry_input_validation(client, dem_id, stockpile_poly):
    url = f"/api/rasters/{dem_id}/volume/"
    assert client.post(url, json={}).status_code == 400
    both = {"geometry": mapping(stockpile_poly), "feature": {"file_id": "x", "index": 0}}
    assert client.post(url, json=both).status_code == 400
    utm = mapping(Point(CX, CY).buffer(10))
    assert "WGS84" in client.post(url, json={"geometry": utm}).json()["error"]["message"]
    assert client.post(url, json={"geometry": {"type": "Nope"}}).status_code == 400


def test_profile_and_altitude_endpoints(client, dem_id):
    line = f.utm_to_wgs84(LineString([(f.DEM_ORIGIN[0] + 5, CY + 40), (f.DEM_ORIGIN[0] + 95, CY + 40)]))
    r = client.post(f"/api/rasters/{dem_id}/profile/", json={"geometry": mapping(line), "max_samples": 200})
    assert r.status_code == 200, r.text
    assert r.json()["stats"]["mean_abs_grade_pct"] == pytest.approx(10, abs=0.1)
    assert len(r.json()["distance_m"]) <= 200

    lon, lat = Transformer.from_crs(f.UTM43, "EPSG:4326", always_xy=True).transform(CX, CY)
    r = client.get(f"/api/rasters/{dem_id}/altitude/", params={"lon": lon, "lat": lat})
    assert r.json()["elevation_m"] == pytest.approx(105 + 10, abs=0.3)
    assert (
        client.get(f"/api/rasters/{dem_id}/altitude/", params={"lon": 0, "lat": 0}).json()["elevation_m"]
        is None
    )


@pytest.mark.parametrize(
    ("data", "name", "status", "match"),
    [
        (b"not a tiff", "dem.tif", 422, "not a readable GeoTIFF"),
        (b"x", "dem.png", 415, "Unsupported file type"),
    ],
)
def test_bad_dem_uploads(client, data, name, status, match):
    r = client.post("/api/rasters/", files={"file": (name, io.BytesIO(data))})
    assert r.status_code == status
    assert match in r.json()["error"]["message"]


def test_dem_without_crs_is_rejected_and_recorded(client):
    z = np.full((20, 20), 100.0)
    r = upload_dem(client, f.dem_bytes(z, crs=None))
    assert r.status_code == 422
    assert "no CRS" in r.json()["error"]["message"]
    assert client.get(f"/api/rasters/{r.json()['error']['file_id']}/").json()["status"] == "FAILED"


def test_imagery_is_rejected(client):
    z = np.full((20, 20), 120)
    r = upload_dem(client, f.dem_bytes(z, dtype="uint8", count=3, nodata=None))
    assert r.status_code == 422
    assert "imagery" in r.json()["error"]["message"]


def test_dem_too_large(client, settings):
    settings.max_raster_upload_bytes = 1000
    r = upload_dem(client, f.dem_bytes(np.full((100, 100), 1.0)))
    assert r.status_code == 413


def test_dem_too_many_cells(client, settings):
    settings.max_raster_pixels = 100
    r = upload_dem(client, f.dem_bytes(np.full((20, 20), 1.0)))
    assert r.status_code == 422
    assert "limit" in r.json()["error"]["message"]


def test_delete_dem_removes_blob(client, dem_id, settings):
    from pathlib import Path

    blob = Path(settings.storage_dir) / "rasters" / f"{dem_id}.tif"
    assert blob.exists()
    assert client.delete(f"/api/rasters/{dem_id}/").status_code == 204
    assert not blob.exists()
    assert client.get(f"/api/rasters/{dem_id}/").status_code == 404


def test_list_rasters(client, dem_id):
    body = client.get("/api/rasters/").json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == dem_id
