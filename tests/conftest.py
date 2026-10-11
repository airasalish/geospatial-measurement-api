from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    # Tests never depend on whether the UI happens to be built locally.
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        frontend_dir=str(tmp_path / "no-ui"),
        storage_dir=str(tmp_path / "blobs"),
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        yield c
