"""Application settings, loaded from environment variables (prefix ``GEO_``)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GEO_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/geo.db"

    # Upload guards. Uploads are streamed and rejected as soon as they exceed the limit.
    max_upload_bytes: int = 50 * 1024 * 1024
    # Zip-bomb guards for shapefile archives / KMZ.
    max_uncompressed_bytes: int = 500 * 1024 * 1024
    max_archive_members: int = 1_000
    max_compression_ratio: int = 200
    # Upper bound on features per file, protects the DB and memory.
    max_features: int = 250_000

    # Pagination defaults for list endpoints.
    default_page_size: int = 100
    max_page_size: int = 1_000
    # Cap for the map endpoint, which returns a whole file in one response.
    max_geojson_features: int = 100_000

    # Comma-separated origins allowed to call the API from a browser, e.g. a separately
    # hosted frontend. The bundled UI is served same-origin and needs none.
    cors_origins: str = ""
    # Directory with the built frontend (frontend/dist). Served at / when it exists.
    frontend_dir: str = "frontend/dist"

    # Elevation models (DEM/DSM GeoTIFFs). Stored as COGs under storage_dir.
    storage_dir: str = "data/blobs"
    max_raster_upload_bytes: int = 500 * 1024 * 1024
    max_raster_pixels: int = 250_000_000

    # Blob storage: "local" (storage_dir) or "s3" (AWS S3 or any S3-compatible store).
    storage_backend: Literal["local", "s3"] = "local"
    s3_bucket: str = "traverse"
    s3_endpoint_url: str | None = None  # e.g. http://s3:8333 (SeaweedFS); None for AWS
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "us-east-1"

    # Processing: "sync" runs in the request (simple, the demo default);
    # "celery" returns 202 and a worker processes the upload from blob storage.
    task_mode: Literal["sync", "celery"] = "sync"
    redis_url: str = "redis://localhost:6379/0"
    celery_eager: bool = False  # run tasks in-process (tests)

    # Observability
    log_format: Literal["text", "json"] = "text"
    samples_dir: str = "samples"


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def resolve_path(path: str) -> Path:
    """Relative settings paths are relative to the project root, not the CWD."""
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


@lru_cache
def get_settings() -> Settings:
    return Settings()
