import pytest
import shapefile
from pyproj import Geod
from shapely.geometry import box
from sqlalchemy import func, select

from app.config import Settings
from app.models import Feature
from tests import factories as f

GEOD = Geod(ellps="WGS84")


def mixed_kml() -> bytes:
    return f.kml(
        f.placemark(f.kml_polygon([(77.59, 12.97), (77.60, 12.97), (77.60, 12.98), (77.59, 12.98)]),
                    name="plot", pid="p1")
        + f.placemark(f.kml_line([(77.59, 12.97), (77.60, 12.98)]), name="road")
        + f.placemark("<Point><coordinates>77.595,12.975</coordinates></Point>", name="tower")
        + f.placemark("<Model><Link><href>x.dae</href></Link></Model>", name="model")
    )  # fmt: skip


def upload(client, name: str, data: bytes):
    return client.post("/api/files/", files={"file": (name, data)})


def test_upload_kml_returns_file_info(client):
    r = upload(client, "survey.kml", mixed_kml())
    assert r.status_code == 201
    body = r.json()
    assert body["filename"] == "survey.kml"
    assert body["format"] == "kml"
    assert body["status"] == "COMPLETED"
    assert body["crs"] == "EPSG:4326"
    assert body["feature_count"] == 4
    assert len(body["sha256"]) == 64
    assert r.headers["location"].endswith(f"/api/files/{body['id']}/")


def test_get_file(client):
    file_id = upload(client, "survey.kml", mixed_kml()).json()["id"]
    r = client.get(f"/api/files/{file_id}/")
    assert r.status_code == 200
    assert r.json()["id"] == file_id
    # trailing slash is optional
    assert client.get(f"/api/files/{file_id}").status_code == 200


def test_measurements(client):
    file_id = upload(client, "survey.kml", mixed_kml()).json()["id"]
    r = client.get(f"/api/files/{file_id}/measurements/")
    assert r.status_code == 200
    body = r.json()
    assert body["file_id"] == file_id
    assert body["units"] == {"area": "m2", "perimeter": "m", "length": "m"}
    poly, line, point, model = body["items"]

    expected_area = abs(GEOD.geometry_area_perimeter(box(77.59, 12.97, 77.60, 12.98))[0])
    assert poly["status"] == "MEASURED"
    assert poly["source_id"] == "p1"
    assert poly["area_m2"] == pytest.approx(expected_area, rel=1e-6)
    assert poly["measurement_crs"]["area"].startswith("+proj=laea")

    assert line["status"] == "MEASURED"
    assert line["length_m"] > 1500
    assert point["status"] == "NOT_APPLICABLE"
    assert model["status"] == "UNSUPPORTED"
    assert "not supported" in model["note"]

    summary = body["summary"]
    assert summary["feature_count"] == 4
    assert summary["total_area_m2"] == pytest.approx(poly["area_m2"])
    assert summary["total_length_m"] == pytest.approx(line["length_m"])
    assert summary["by_status"] == {"MEASURED": 2, "NOT_APPLICABLE": 1, "UNSUPPORTED": 1}
    assert summary["by_geometry_type"] == {"Polygon": 1, "LineString": 1, "Point": 1, "Model": 1}


def test_measurements_filters_and_pagination(client):
    file_id = upload(client, "survey.kml", mixed_kml()).json()["id"]
    r = client.get(f"/api/files/{file_id}/measurements/", params={"status": "MEASURED"})
    assert [i["geometry_type"] for i in r.json()["items"]] == ["Polygon", "LineString"]
    assert r.json()["total"] == 2
    # summary always describes the whole file
    assert r.json()["summary"]["feature_count"] == 4

    r = client.get(f"/api/files/{file_id}/measurements/", params={"geometry_type": "point"})
    assert [i["index"] for i in r.json()["items"]] == [2]

    r = client.get(f"/api/files/{file_id}/measurements/", params={"limit": 1, "offset": 1})
    assert [i["index"] for i in r.json()["items"]] == [1]
    assert r.json()["total"] == 4

    assert client.get(f"/api/files/{file_id}/measurements/", params={"status": "NOPE"}).status_code == 422
    assert client.get(f"/api/files/{file_id}/measurements/", params={"limit": 0}).status_code == 422


def test_page_size_is_capped(client, settings: Settings):
    file_id = upload(client, "survey.kml", mixed_kml()).json()["id"]
    r = client.get(f"/api/files/{file_id}/measurements/", params={"limit": 10**6})
    assert r.json()["limit"] == settings.max_page_size


def test_features_endpoint_returns_geometry_crs_properties(client):
    file_id = upload(client, "survey.kml", mixed_kml()).json()["id"]
    body = client.get(f"/api/files/{file_id}/features/").json()
    assert body["total"] == 4
    first = body["items"][0]
    assert first["index"] == 0
    assert first["geometry_type"] == "Polygon"
    assert first["geometry"]["type"] == "Polygon"
    assert first["crs"] == "EPSG:4326"
    assert first["properties"] == {"name": "plot"}
    assert body["items"][3]["geometry"] is None

    single = client.get(f"/api/files/{file_id}/features/1/").json()
    assert single["geometry_type"] == "LineString"
    assert client.get(f"/api/files/{file_id}/features/99/").status_code == 404


def test_upload_zipped_shapefile_in_projected_crs(client):
    # Two 1 km x 1 km squares in UTM 43N.
    sq = [[(500_000, 1_430_000), (500_000, 1_431_000), (501_000, 1_431_000), (501_000, 1_430_000),
           (500_000, 1_430_000)]]  # fmt: skip
    data = f.shapefile_zip(shape_type=shapefile.POLYGON, geometries=[sq, sq], epsg=32643)
    r = upload(client, "parcels.zip", data)
    assert r.status_code == 201
    assert r.json()["format"] == "shapefile"
    assert r.json()["crs"] == "EPSG:32643"
    assert r.json()["layers"] == ["parcels"]
    items = client.get(f"/api/files/{r.json()['id']}/measurements/").json()["items"]
    assert all(i["area_m2"] == pytest.approx(1_000_000, rel=1e-3) for i in items)


def test_shapefile_without_prj_is_processed_with_warning(client):
    data = f.shapefile_zip(shape_type=shapefile.POLYGON, geometries=[f.square(77.59, 12.97, 0.01)], epsg=None)
    body = upload(client, "nocrs.zip", data).json()
    assert body["status"] == "COMPLETED"
    assert body["crs"] is None
    assert any("no .prj" in w for w in body["warnings"])
    (item,) = client.get(f"/api/files/{body['id']}/measurements/").json()["items"]
    assert item["status"] == "MEASURED"
    assert "assumed" in item["note"]


def test_mixed_crs_layers(client):
    a = f.shapefile_parts(shape_type=shapefile.POINT, geometries=[(1, 2)])
    b = f.shapefile_parts(shape_type=shapefile.POINT, geometries=[(500_000, 1_430_000)])
    members = {f"a{e}": v for e, v in a.items()} | {f"b{e}": v for e, v in b.items()}
    members |= {"a.prj": f.prj(4326), "b.prj": f.prj(32643)}
    body = upload(client, "mixed.zip", f.zip_bytes(members)).json()
    assert body["crs"] == "MIXED"
    feats = client.get(f"/api/files/{body['id']}/features/").json()["items"]
    assert [x["crs"] for x in feats] == ["EPSG:4326", "EPSG:32643"]


def test_unsupported_file_type(client):
    r = upload(client, "data.geojson", b"{}")
    assert r.status_code == 415
    assert r.json()["error"]["code"] == "unsupported_file_type"


def test_invalid_file_is_recorded_as_failed(client):
    r = upload(client, "broken.kml", b"<kml><Document>")
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "invalid_file"
    record = client.get(f"/api/files/{err['file_id']}/").json()
    assert record["status"] == "FAILED"
    assert record["error"] == err["message"]
    assert record["feature_count"] == 0


def test_failure_mid_file_leaves_no_partial_features(client, settings: Settings):
    settings.max_features = 2
    r = upload(client, "survey.kml", mixed_kml())  # 4 features
    assert r.status_code == 422
    file_id = r.json()["error"]["file_id"]
    assert client.get(f"/api/files/{file_id}/features/").json()["total"] == 0


def test_upload_too_large(client, settings: Settings):
    settings.max_upload_bytes = 100
    r = upload(client, "big.kml", b"x" * 101)
    assert r.status_code == 413


def test_upload_requires_file(client):
    assert client.post("/api/files/").status_code == 422


def test_client_supplied_path_is_stripped(client):
    r = upload(client, "C:\\fakepath\\..\\survey.kml", mixed_kml())
    assert r.json()["filename"] == "survey.kml"


def test_not_found(client):
    for path in ("/api/files/nope/", "/api/files/nope/measurements/", "/api/files/nope/features/"):
        r = client.get(path)
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "not_found"


def test_list_and_delete(client):
    ids = [upload(client, f"{i}.kml", mixed_kml()).json()["id"] for i in range(3)]
    listing = client.get("/api/files/", params={"limit": 2}).json()
    assert listing["total"] == 3
    assert len(listing["items"]) == 2

    assert client.delete(f"/api/files/{ids[0]}/").status_code == 204
    assert client.get(f"/api/files/{ids[0]}/").status_code == 404
    assert client.get(f"/api/files/{ids[0]}/features/").status_code == 404
    assert client.get("/api/files/").json()["total"] == 2
    # Feature rows are removed by the database's ON DELETE CASCADE.
    with client.app.state.db.session_factory() as session:
        remaining = session.scalar(select(func.count()).select_from(Feature).where(Feature.file_id == ids[0]))
    assert remaining == 0


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}
