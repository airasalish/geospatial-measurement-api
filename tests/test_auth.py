"""Accounts, tokens, projects and tenant isolation (GEO_AUTH_REQUIRED=true)."""

import io
from datetime import UTC, datetime, timedelta

import jwt
import morecantile
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests import factories as f

SECRET = "test-secret-" + "x" * 32


@pytest.fixture
def auth_client(settings: Settings):
    settings.auth_required = True
    settings.jwt_secret = SECRET
    with TestClient(create_app(settings)) as c:
        yield c


def signup(client: TestClient, email: str, password: str = "correct horse battery") -> dict[str, str]:
    assert client.post("/api/auth/register", json={"email": email, "password": password}).status_code == 201
    r = client.post("/api/auth/token", data={"username": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def kml_upload(client: TestClient, headers: dict[str, str], **data: str):
    body = f.kml(f.placemark(f.kml_polygon([(77.59, 12.97), (77.6, 12.97), (77.6, 12.98), (77.59, 12.98)])))
    return client.post("/api/files/", files={"file": ("plot.kml", body)}, data=data, headers=headers)


def test_config_endpoint_when_required(auth_client):
    assert auth_client.get("/api/auth/config").json() == {"auth_required": True}


def test_config_endpoint_when_open(client):
    assert client.get("/api/auth/config").json() == {"auth_required": False}


def test_register_login_me(auth_client):
    headers = signup(auth_client, "Asha@Example.com")
    me = auth_client.get("/api/auth/me", headers=headers).json()
    assert me["email"] == "asha@example.com"  # normalised


def test_register_validation_and_duplicates(auth_client):
    assert (
        auth_client.post("/api/auth/register", json={"email": "nope", "password": "longenough"}).status_code
        == 422
    )
    assert (
        auth_client.post("/api/auth/register", json={"email": "a@b.co", "password": "short"}).status_code
        == 422
    )
    signup(auth_client, "a@b.co")
    r = auth_client.post("/api/auth/register", json={"email": "A@B.co", "password": "another-password"})
    assert r.status_code == 409


def test_wrong_password_and_unknown_user_look_the_same(auth_client):
    signup(auth_client, "a@b.co")
    wrong = auth_client.post("/api/auth/token", data={"username": "a@b.co", "password": "nope-nope"})
    unknown = auth_client.post("/api/auth/token", data={"username": "z@b.co", "password": "nope-nope"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_endpoints_require_sign_in(auth_client):
    for path in ("/api/files/", "/api/rasters/", "/api/projects/", "/api/auth/me"):
        r = auth_client.get(path)
        assert r.status_code == 401, path
        assert r.json()["error"]["code"] == "unauthorized"


def test_tenant_isolation(auth_client):
    alice = signup(auth_client, "alice@site.io")
    bob = signup(auth_client, "bob@site.io")
    file_id = kml_upload(auth_client, alice).json()["id"]

    assert auth_client.get(f"/api/files/{file_id}/", headers=alice).status_code == 200
    # Bob sees nothing, and can't tell whether the id exists.
    assert auth_client.get("/api/files/", headers=bob).json()["total"] == 0
    for path in (
        f"/api/files/{file_id}/",
        f"/api/files/{file_id}/measurements/",
        f"/api/files/{file_id}/geojson/",
    ):
        assert auth_client.get(path, headers=bob).status_code == 404, path
    assert auth_client.delete(f"/api/files/{file_id}/", headers=bob).status_code == 404
    assert auth_client.get(f"/api/files/{file_id}/", headers=alice).status_code == 200


def test_dem_isolation_and_feature_refs(auth_client):
    alice = signup(auth_client, "alice@site.io")
    bob = signup(auth_client, "bob@site.io")
    x, _ = f.dem_grid(n=40, res=1.0)
    dem = auth_client.post(
        "/api/rasters/",
        files={"file": ("d.tif", io.BytesIO(f.dem_bytes(100 + 0 * x, res=1.0)))},
        headers=alice,
    ).json()["id"]
    file_id = kml_upload(auth_client, alice).json()["id"]
    assert auth_client.get(f"/api/rasters/{dem}/", headers=bob).status_code == 404
    bob_dem = auth_client.post(
        "/api/rasters/", files={"file": ("d.tif", io.BytesIO(f.dem_bytes(100 + 0 * x, res=1.0)))}, headers=bob
    ).json()["id"]
    # Bob can't measure his DEM against Alice's polygon, nor against Alice's DEM.
    r = auth_client.post(
        f"/api/rasters/{bob_dem}/volume/", json={"feature": {"file_id": file_id, "index": 0}}, headers=bob
    )
    assert r.status_code == 404
    poly = {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [0, 0]]]}
    r = auth_client.post(
        f"/api/rasters/{bob_dem}/volume/",
        json={"geometry": poly, "base": {"method": "surface", "raster_id": dem}},
        headers=bob,
    )
    assert r.status_code == 404


def test_tiles_accept_access_token_query(auth_client):
    alice = signup(auth_client, "alice@site.io")
    file_id = kml_upload(auth_client, alice).json()["id"]
    token = alice["Authorization"].split()[1]
    t = morecantile.tms.get("WebMercatorQuad").tile(77.595, 12.975, 14)
    url = f"/api/files/{file_id}/tiles/{t.z}/{t.x}/{t.y}.mvt"
    assert auth_client.get(url).status_code == 401
    assert auth_client.get(url, params={"access_token": token}).status_code == 200


def test_projects(auth_client):
    alice = signup(auth_client, "alice@site.io")
    bob = signup(auth_client, "bob@site.io")
    pit = auth_client.post("/api/projects/", json={"name": "North pit"}, headers=alice).json()
    assert auth_client.post("/api/projects/", json={"name": "North pit"}, headers=alice).status_code == 409
    in_pit = kml_upload(auth_client, alice, project_id=pit["id"]).json()["id"]
    kml_upload(auth_client, alice)  # unassigned
    listing = auth_client.get("/api/files/", params={"project_id": pit["id"]}, headers=alice).json()
    assert [i["id"] for i in listing["items"]] == [in_pit]
    assert auth_client.get("/api/projects/", headers=alice).json()[0]["files"] == 1
    # Bob can't file uploads under Alice's project, or see it.
    assert kml_upload(auth_client, bob, project_id=pit["id"]).status_code == 404
    assert auth_client.get("/api/projects/", headers=bob).json() == []
    assert auth_client.delete(f"/api/projects/{pit['id']}/", headers=bob).status_code == 404
    # Deleting a project keeps its files.
    assert auth_client.delete(f"/api/projects/{pit['id']}/", headers=alice).status_code == 204
    assert auth_client.get(f"/api/files/{in_pit}/", headers=alice).status_code == 200


def test_expired_and_tampered_tokens(auth_client):
    alice = signup(auth_client, "alice@site.io")
    sub = jwt.decode(alice["Authorization"].split()[1], SECRET, algorithms=["HS256"])["sub"]
    expired = jwt.encode(
        {"sub": sub, "exp": datetime.now(UTC) - timedelta(minutes=1)}, SECRET, algorithm="HS256"
    )
    r = auth_client.get("/api/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401
    assert "expired" in r.json()["error"]["message"]
    forged = jwt.encode(
        {"sub": sub, "exp": datetime.now(UTC) + timedelta(hours=1)},
        "a-different-secret-" + "y" * 32,
        algorithm="HS256",
    )
    assert auth_client.get("/api/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_refuses_default_or_short_secret(settings: Settings):
    settings.auth_required = True
    with pytest.raises(RuntimeError, match="GEO_JWT_SECRET"):
        create_app(settings)
    settings.jwt_secret = "too-short"
    with pytest.raises(RuntimeError, match="32"):
        create_app(settings)


def test_password_hashes_are_salted():
    from app.auth import hash_password, verify_password

    a, b = hash_password("same password"), hash_password("same password")
    assert a != b
    assert verify_password("same password", a)
    assert not verify_password("other password", a)
    assert not verify_password("x", "not-a-hash")
