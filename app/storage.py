"""Blob storage for large binary artefacts (DEMs as Cloud-Optimised GeoTIFFs).

The service stores *keys*, never paths, so the backend can change. The local
filesystem works in development; S3/MinIO is selected with ``GEO_STORAGE_BACKEND=s3``.
Readers get a URL that GDAL can open directly (a local path, or ``/vsis3/...``),
so COGs on S3 are read with HTTP range requests instead of being downloaded.
"""

import shutil
from pathlib import Path
from typing import Protocol


class Storage(Protocol):
    def put(self, local_path: Path, key: str) -> None: ...
    def gdal_path(self, key: str) -> str: ...
    def delete(self, key: str) -> None: ...


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError(f"Invalid storage key {key!r}")
        return path

    def put(self, local_path: Path, key: str) -> None:
        dst = self._path(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(local_path), dst)

    def gdal_path(self, key: str) -> str:
        return str(self._path(key))

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)
