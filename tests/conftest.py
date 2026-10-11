"""Test fixtures.

By default every test gets a fresh SQLite database. Set GEO_TEST_DATABASE_URL
(e.g. postgresql+psycopg://postgres:postgres@localhost:5432/geo) to run the same
suite against PostgreSQL/PostGIS. CI does this with a service container. Tables
are truncated between tests in that mode.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.config import Settings
from app.main import create_app

PG_URL = os.environ.get("GEO_TEST_DATABASE_URL")


def pytest_report_header() -> str:
    return f"database: {'PostgreSQL ' + PG_URL.split('@')[-1] if PG_URL else 'SQLite (per test)'}"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    # Tests never depend on whether the UI happens to be built locally.
    return Settings(
        database_url=PG_URL or f"sqlite:///{tmp_path / 'test.db'}",
        frontend_dir=str(tmp_path / "no-ui"),
        storage_dir=str(tmp_path / "blobs"),
    )


@pytest.fixture(autouse=True)
def _clean_postgres() -> Iterator[None]:
    yield
    if PG_URL:
        engine = create_engine(PG_URL)
        with engine.begin() as conn:
            if conn.execute(text("SELECT to_regclass('public.files')")).scalar():
                conn.execute(text("TRUNCATE files, rasters, projects, users RESTART IDENTITY CASCADE"))
        engine.dispose()


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        yield c


INTEGRATION = os.environ.get("GEO_INTEGRATION") == "1"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if INTEGRATION:
        return
    skip = pytest.mark.skip(reason="needs the docker services (set GEO_INTEGRATION=1)")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def integration_client() -> Iterator[TestClient]:
    """The API configured from GEO_* environment (same as the running worker)."""
    from app.config import Settings as EnvSettings

    with TestClient(create_app(EnvSettings())) as c:
        yield c
