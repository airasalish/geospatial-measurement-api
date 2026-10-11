"""Acceptance tests: one test per item of the assignment brief, in the brief's order.

The other test modules go deeper; this file is the checklist. Each test names the
requirement it covers so a reviewer can map brief -> code -> proof at a glance.
"""

import pytest
import shapefile
from pyproj import Geod
from shapely.geometry import LineString, box, shape

from tests import factories as f
from tests.test_api import mixed_kml, upload

GEOD = Geod(ellps="WGS84")
PLOT = box(77.59, 12.97, 77.60, 12.98)  # ~1.2 km^2 in Bengaluru
ROAD = LineString([(77.59, 12.97), (77.60, 12.98)])


def geodesic_area(geom) -> float:
    return abs(GEOD.geometry_area_perimeter(geom)[0])


def wgs84_shapefile() -> bytes:
    """A zipped EPSG:4326 shapefile layer per geometry type, all in one archive."""
    polys = f.shapefile_parts(
        shape_type=shapefile.POLYGON,
        geometries=[[list(PLOT.exterior.coords)[::-1]]],  # shapefile rings are clockwise
        records=[{"name": "plot"}],
    )
    lines = f.shapefile_parts(shape_type=shapefile.POLYLINE, geometries=[[list(ROAD.coords)]],
                              records=[{"name": "road"}])  # fmt: skip
    points = f.shapefile_parts(shape_type=shapefile.POINT, geometries=[(77.595, 12.975)],
                               records=[{"name": "tower"}])  # fmt: skip
    members: dict[str, bytes | str] = {}
    for stem, parts in (("plots", polys), ("roads", lines), ("towers", points)):
        members |= {f"{stem}{ext}": data for ext, data in parts.items()}
        members[f"{stem}.prj"] = f.prj(4326)
    return f.zip_bytes(members)


# 1. Backend framework: FastAPI ------------------------------------------------


def test_1_backend_is_fastapi(client):
    from fastapi import FastAPI

    assert isinstance(client.app, FastAPI)
    assert client.get("/openapi.json").status_code == 200


# 2. File upload: POST /api/files/ accepts a zipped Shapefile and a .kml -------


@pytest.mark.parametrize(
    ("name", "data", "fmt"),
    [("survey.kml", mixed_kml(), "kml"), ("survey.zip", wgs84_shapefile(), "shapefile")],
)
def test_2_upload_accepts_kml_and_zipped_shapefile(client, name, data, fmt):
    r = upload(client, name, data)
    assert r.status_code == 201
    assert r.json()["format"] == fmt
    assert r.json()["status"] == "COMPLETED"


# 3. File processing: every feature has ID, geometry type, geometry, CRS, properties


@pytest.mark.parametrize(("name", "data"), [("survey.kml", mixed_kml()), ("survey.zip", wgs84_shapefile())])
def test_3_every_feature_has_id_type_geometry_crs_properties(client, name, data):
    file_id = upload(client, name, data).json()["id"]
    body = client.get(f"/api/files/{file_id}/measurements/", params={"include_geometry": True}).json()
    measurable = [i for i in body["items"] if i["geometry"] is not None]
    assert {i["geometry_type"] for i in measurable} >= {"Polygon", "LineString", "Point"}
    for i, item in enumerate(body["items"]):
        assert item["index"] == i
        assert item["crs"] == "EPSG:4326"
        assert item["properties"]["name"]
    for item in measurable:
        assert shape(item["geometry"]).geom_type == item["geometry_type"]


def test_3_unsupported_geometry_is_reported_not_crashed(client):
    file_id = upload(client, "survey.kml", mixed_kml()).json()["id"]  # contains a KML <Model>
    items = client.get(f"/api/files/{file_id}/measurements/").json()["items"]
    model = next(i for i in items if i["geometry_type"] == "Model")
    assert model["status"] == "UNSUPPORTED"
    assert model["area_m2"] is None
    assert model["length_m"] is None
    assert model["note"]


# 4. Measurements: polygon area, line length, nothing for points ---------------


def test_4_polygon_area_line_length_point_nothing(client):
    file_id = upload(client, "survey.zip", wgs84_shapefile()).json()["id"]
    items = {i["geometry_type"]: i for i in client.get(f"/api/files/{file_id}/measurements/").json()["items"]}

    assert items["Polygon"]["area_m2"] == pytest.approx(geodesic_area(PLOT), rel=1e-6)
    assert items["LineString"]["length_m"] == pytest.approx(GEOD.geometry_length(ROAD), rel=1e-6)
    point = items["Point"]
    assert point["status"] == "NOT_APPLICABLE"
    assert point["area_m2"] is None
    assert point["length_m"] is None


# 5. CRS handling: never measure in degrees; project first ---------------------


def test_5_measured_in_a_projected_crs_not_degrees(client):
    file_id = upload(client, "survey.kml", mixed_kml()).json()["id"]
    items = client.get(f"/api/files/{file_id}/measurements/").json()["items"]
    poly, line = items[0], items[1]

    # The planar area of the lon/lat box would be 1e-4 "square degrees".
    assert PLOT.area == pytest.approx(1e-4)
    assert poly["area_m2"] > 1e6
    # Each value records the projected CRS it was computed in.
    assert poly["measurement_crs"]["area"].startswith("+proj=laea")
    assert line["measurement_crs"]["length"].startswith("+proj=aeqd")


def test_5_projected_input_is_handled_too(client):
    sq = [[(500_000, 1_430_000), (500_000, 1_431_000), (501_000, 1_431_000), (501_000, 1_430_000),
           (500_000, 1_430_000)]]  # fmt: skip
    body = upload(
        client, "utm.zip", f.shapefile_zip(shape_type=shapefile.POLYGON, geometries=[sq], epsg=32643)
    ).json()
    assert body["crs"] == "EPSG:32643"
    (item,) = client.get(f"/api/files/{body['id']}/measurements/").json()["items"]
    assert item["area_m2"] == pytest.approx(1_000_000, rel=1e-3)  # 1 km^2 on the ground


# 6. API design: the three required endpoints and the example response shape ---


def test_6_file_information_matches_the_brief_example(client):
    file_id = upload(client, "survey.kml", mixed_kml()).json()["id"]
    body = client.get(f"/api/files/{file_id}/").json()
    assert {"id", "filename", "feature_count", "crs", "status"} <= body.keys()
    assert (body["id"], body["filename"], body["feature_count"], body["crs"], body["status"]) == (
        file_id, "survey.kml", 4, "EPSG:4326", "COMPLETED",
    )  # fmt: skip


def test_6_measurements_endpoint(client):
    file_id = upload(client, "survey.kml", mixed_kml()).json()["id"]
    body = client.get(f"/api/files/{file_id}/measurements/").json()
    assert body["file_id"] == file_id
    assert body["filename"] == "survey.kml"
    assert body["crs"] == "EPSG:4326"
    assert body["units"] == {"area": "m2", "perimeter": "m", "length": "m"}
    assert body["total"] == len(body["items"]) == 4
    assert "geometry" not in body["items"][0] or body["items"][0]["geometry"] is None


def test_6_errors_are_http_status_codes_with_json_bodies(client):
    assert client.get("/api/files/missing/").status_code == 404
    assert upload(client, "notes.txt", b"hello").status_code == 415
    r = upload(client, "broken.kml", b"<kml><Document>")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_file"
