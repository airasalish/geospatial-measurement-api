"""Spatial filtering and Mapbox Vector Tiles (SQLite path locally, PostGIS in CI)."""

import mapbox_vector_tile
import morecantile
import pytest
from sqlalchemy import text

from tests import factories as f
from tests.conftest import PG_URL

WM = morecantile.tms.get("WebMercatorQuad")


def grid_kml() -> bytes:
    """Nine 0.01-degree squares in a 3x3 grid near Bengaluru, named by row/col."""
    body = ""
    for r in range(3):
        for c in range(3):
            x, y = 77.50 + 0.02 * c, 12.90 + 0.02 * r
            ring = [(x, y), (x + 0.01, y), (x + 0.01, y + 0.01), (x, y + 0.01), (x, y)]
            body += f.placemark(f.kml_polygon(ring), name=f"r{r}c{c}")
    return f.kml(body)


@pytest.fixture
def grid_id(client):
    return client.post("/api/files/", files={"file": ("grid.kml", grid_kml())}).json()["id"]


def test_bbox_filter(client, grid_id):
    # Covers only the bottom-left cell (and not its neighbours 0.01 deg away).
    r = client.get(f"/api/files/{grid_id}/features/", params={"bbox": "77.495,12.895,77.505,12.905"})
    assert [i["properties"]["name"] for i in r.json()["items"]] == ["r0c0"]
    r = client.get(f"/api/files/{grid_id}/measurements/", params={"bbox": "77.49,12.89,77.555,12.915"})
    assert r.json()["total"] == 3  # bottom row
    assert r.json()["summary"]["feature_count"] == 9  # summary still covers the file


@pytest.mark.parametrize("bad", ["1,2,3", "a,b,c,d", "10,10,0,0", "0,0,200,10"])
def test_bbox_validation(client, grid_id, bad):
    r = client.get(f"/api/files/{grid_id}/features/", params={"bbox": bad})
    assert r.status_code == 400


def test_vector_tile(client, grid_id):
    t = WM.tile(77.505, 12.905, 14)
    r = client.get(f"/api/files/{grid_id}/tiles/{t.z}/{t.x}/{t.y}.mvt")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/vnd.mapbox-vector-tile"
    layer = mapbox_vector_tile.decode(r.content)["features"]
    names = sorted(feat["properties"]["name"] for feat in layer["features"])
    assert "r0c0" in names
    props = next(feat["properties"] for feat in layer["features"] if feat["properties"]["name"] == "r0c0")
    assert props["status"] == "MEASURED"
    assert props["area_m2"] > 1_000_000
    assert layer["extent"] == 4096


def test_vector_tile_empty_and_invalid(client, grid_id):
    far = WM.tile(0, 0, 14)
    assert client.get(f"/api/files/{grid_id}/tiles/{far.z}/{far.x}/{far.y}.mvt").status_code == 204
    assert client.get(f"/api/files/{grid_id}/tiles/2/9/0.mvt").status_code == 400
    assert client.get("/api/files/nope/tiles/1/0/0.mvt").status_code == 404


@pytest.mark.skipif(not PG_URL, reason="PostGIS-only")
def test_postgis_geometry_column_is_populated(client, grid_id):
    from sqlalchemy import create_engine

    engine = create_engine(PG_URL)
    with engine.connect() as conn:
        n, srid = conn.execute(
            text("SELECT count(geom), max(ST_SRID(geom)) FROM features WHERE file_id = :f"), {"f": grid_id}
        ).one()
        assert (n, srid) == (9, 4326)
        # The spatial index exists and the planner can use it.
        assert conn.execute(text("SELECT to_regclass('ix_features_geom')")).scalar() is not None
    engine.dispose()
