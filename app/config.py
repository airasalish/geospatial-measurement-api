"""Application settings, loaded from environment variables (prefix ``GEO_``)."""

from functools import lru_cache

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
    samples_dir: str = "samples"


@lru_cache
def get_settings() -> Settings:
    return Settings()
