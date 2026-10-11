"""Background processing, storage backends, health and metrics."""

import io
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.storage import LocalStorage, S3Storage
from tests import factories as f


@pytest.fixture
def celery_client(settings: Settings):
    """The API in GEO_TASK_MODE=celery, with tasks executed eagerly in-process."""
    settings.task_mode = "celery"
    settings.celery_eager = True
    with TestClient(create_app(settings)) as c:
        yield c


def test_celery_mode_returns_202_and_completes(celery_client):
    kml = f.kml(f.placemark(f.kml_line([(77.59, 12.97), (77.60, 12.98)])))
    r = celery_client.post("/api/files/", files={"file": ("road.kml", kml)})
    assert r.status_code == 202
    assert r.headers["location"].endswith(f"/api/files/{r.json()['id']}/")
    # Eager mode has already run the task; a client polls the Location URL.
    body = celery_client.get(r.headers["location"]).json()
    assert body["status"] == "COMPLETED"
    assert body["feature_count"] == 1


def test_celery_mode_records_failures(celery_client):
    r = celery_client.post("/api/files/", files={"file": ("bad.kml", b"<kml><Document>")})
    assert r.status_code == 202  # accepted: the failure happens in the worker
    body = celery_client.get(f"/api/files/{r.json()['id']}/").json()
    assert body["status"] == "FAILED"
    assert "well-formed" in body["error"]


def test_celery_mode_raster(celery_client, settings: Settings):
    x, _ = f.dem_grid(n=60, res=1.0)
    r = celery_client.post(
        "/api/rasters/", files={"file": ("dem.tif", io.BytesIO(f.dem_bytes(100 + 0 * x, res=1.0)))}
    )
    assert r.status_code == 202
    body = celery_client.get(f"/api/rasters/{r.json()['id']}/").json()
    assert body["status"] == "COMPLETED"
    # The raw upload is removed once processed; only the COG remains.
    blobs = sorted(
        p.relative_to(settings.storage_dir).as_posix() for p in Path(settings.storage_dir).rglob("*.tif")
    )
    assert blobs == [f"rasters/{body['id']}.tif"]


def test_metrics_endpoint(client):
    client.get("/health")
    client.post("/api/files/", files={"file": ("a.kml", f.kml(f.placemark(f.kml_line([(0, 0), (1, 1)]))))})
    text = client.get("/metrics").text
    assert 'traverse_http_requests_total{method="GET",route="/health",status="200"}' in text
    assert 'traverse_uploads_processed_total{kind="vector",status="COMPLETED"}' in text
    assert "traverse_http_request_duration_seconds_bucket" in text
    # Route templates, not raw ids, keep label cardinality bounded.
    assert 'route="/api/files/"' in text


def test_readiness(client):
    r = client.get("/health/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "checks": {"database": "ok", "storage": "ok"}}


def test_readiness_reports_broken_dependency(settings: Settings):
    settings.task_mode = "celery"
    settings.celery_eager = False
    settings.redis_url = "redis://127.0.0.1:1/0"  # nothing listens here
    with TestClient(create_app(settings)) as c:
        r = c.get("/health/ready")
    assert r.status_code == 503
    assert r.json()["status"] == "degraded"
    assert r.json()["checks"]["queue"].startswith("error")


def test_local_storage_rejects_path_traversal(tmp_path):
    store = LocalStorage(tmp_path)
    store.put_bytes(b"x", "a/b.bin")
    assert store.read_bytes("a/b.bin") == b"x"
    with pytest.raises(ValueError, match="Invalid storage key"):
        store.read_bytes("../outside.bin")


def test_s3_gdal_environment_for_self_hosted_s3():
    s3 = S3Storage("traverse", "http://s3:8333", "key", "secret")
    env = s3.gdal_env()
    assert s3.gdal_path("rasters/x.tif") == "/vsis3/traverse/rasters/x.tif"
    assert env["AWS_S3_ENDPOINT"] == "s3:8333"
    assert env["AWS_HTTPS"] == "NO"
    assert env["AWS_VIRTUAL_HOSTING"] == "FALSE"
    assert env["AWS_ACCESS_KEY_ID"] == "key"


# --------------------------------------------------------------------------
# Integration: real worker, Redis, S3 and PostGIS (CI "compose" job).


@pytest.mark.integration
def test_full_stack_upload_through_worker(integration_client):
    """Upload -> 202 -> worker (separate process) -> COMPLETED, DEM read from S3."""
    c = integration_client
    kml = f.kml(f.placemark(f.kml_polygon([(77.59, 12.97), (77.6, 12.97), (77.6, 12.98), (77.59, 12.98)])))
    file_id = c.post("/api/files/", files={"file": ("plot.kml", kml)}).json()["id"]
    x, _ = f.dem_grid(n=80, res=1.0)
    dem_id = c.post(
        "/api/rasters/", files={"file": ("dem.tif", io.BytesIO(f.dem_bytes(100 + 0.05 * x, res=1.0)))}
    ).json()["id"]

    def wait(url: str) -> dict:
        for _ in range(120):
            body = c.get(url).json()
            if body["status"] != "PROCESSING":
                return body
            time.sleep(0.5)
        raise AssertionError(f"{url} still processing")

    assert wait(f"/api/files/{file_id}/")["status"] == "COMPLETED"
    dem = wait(f"/api/rasters/{dem_id}/")
    assert dem["status"] == "COMPLETED", dem
    # Tiles and analyses read the COG straight from S3 via /vsis3/.
    lon, lat = (dem["bbox"][0] + dem["bbox"][2]) / 2, (dem["bbox"][1] + dem["bbox"][3]) / 2
    assert c.get(f"/api/rasters/{dem_id}/altitude/", params={"lon": lon, "lat": lat}).json()["elevation_m"]
    assert np.isfinite(c.get(f"/api/rasters/{dem_id}/").json()["z_max"])
    assert c.get("/health/ready").json()["checks"] == {"database": "ok", "storage": "ok", "queue": "ok"}
    r = c.get(f"/api/rasters/{dem_id}/download/", follow_redirects=False)
    assert r.status_code == 307
    assert "X-Amz-Signature" in r.headers["location"]
