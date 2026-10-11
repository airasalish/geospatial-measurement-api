"""Blob storage for uploads and Cloud-Optimised GeoTIFFs.

The service stores *keys*, never paths, so the backend can change:

* ``LocalStorage``: a directory. Development, tests and the single-container demo.
* ``S3Storage``: S3 or any S3-compatible store (SeaweedFS in docker-compose).

Rasters are never downloaded to be read. ``gdal_path`` returns a path GDAL opens
directly (a local path, or ``/vsis3/bucket/key``), and ``rasterio_env()`` returns
the matching rasterio environment: endpoint options for GDAL, plus credentials
through rasterio's ``AWSSession``, since rasterio refuses AWS keys as plain GDAL
options. A COG on S3 is then read with HTTP range requests, fetching only the
blocks a tile or polygon needs.
"""

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlparse

if TYPE_CHECKING:
    from app.config import Settings


class Storage(Protocol):
    def put(self, local_path: Path, key: str) -> None: ...
    def put_bytes(self, data: bytes, key: str) -> None: ...
    def read_bytes(self, key: str) -> bytes: ...
    def download(self, key: str, local_path: Path) -> None: ...
    def gdal_path(self, key: str) -> str: ...
    def gdal_env(self) -> dict[str, str]: ...
    def rasterio_env(self) -> Any: ...
    def local_path(self, key: str) -> Path | None: ...
    def delete(self, key: str) -> None: ...
    def check(self) -> None: ...


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

    def put_bytes(self, data: bytes, key: str) -> None:
        dst = self._path(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)

    def read_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def download(self, key: str, local_path: Path) -> None:
        shutil.copyfile(self._path(key), local_path)

    def gdal_path(self, key: str) -> str:
        return str(self._path(key))

    def gdal_env(self) -> dict[str, str]:
        return {}

    def rasterio_env(self) -> Any:
        import rasterio

        return rasterio.Env()

    def local_path(self, key: str) -> Path | None:
        return self._path(key)

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def check(self) -> None:
        probe = self.root / ".ready"
        probe.write_text("ok")
        probe.unlink()


class S3Storage:
    def __init__(
        self,
        bucket: str,
        endpoint_url: str | None,
        access_key: str | None,
        secret_key: str | None,
        region: str = "us-east-1",
    ) -> None:
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        self.endpoint_url = endpoint_url
        self.region = region
        self._access_key = access_key
        self._secret_key = secret_key
        self.client: Any = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=Config(
                signature_version="s3v4",  # SigV4 presigned URLs (SigV2 is legacy)
                s3={"addressing_style": "path"},
                retries={"max_attempts": 5, "mode": "standard"},
            ),
        )

    def ensure_bucket(self) -> None:
        """Create the bucket if needed. The API and the worker may race to do this."""
        from botocore.exceptions import ClientError

        try:
            self.client.head_bucket(Bucket=self.bucket)
            return
        except ClientError:
            pass
        try:
            self.client.create_bucket(Bucket=self.bucket)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in (
                "BucketAlreadyOwnedByYou",
                "BucketAlreadyExists",
            ):
                raise

    def put(self, local_path: Path, key: str) -> None:
        self.client.upload_file(str(local_path), self.bucket, key)
        local_path.unlink(missing_ok=True)

    def put_bytes(self, data: bytes, key: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)

    def read_bytes(self, key: str) -> bytes:
        body: bytes = self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        return body

    def download(self, key: str, local_path: Path) -> None:
        self.client.download_file(self.bucket, key, str(local_path))

    def gdal_path(self, key: str) -> str:
        return f"/vsis3/{self.bucket}/{key}"

    def gdal_env(self) -> dict[str, str]:
        env = {
            "AWS_REGION": self.region,
            "AWS_VIRTUAL_HOSTING": "FALSE",  # path-style, as self-hosted S3 stores expect
            "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",  # don't LIST the bucket on open
            "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.tiff",
        }
        if self.endpoint_url:
            parsed = urlparse(self.endpoint_url)
            env["AWS_S3_ENDPOINT"] = parsed.netloc
            env["AWS_HTTPS"] = "YES" if parsed.scheme == "https" else "NO"
        return env

    def rasterio_env(self) -> Any:
        import rasterio
        from rasterio.session import AWSSession

        session = AWSSession(
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
            region_name=self.region,
        )
        return rasterio.Env(session=session, **self.gdal_env())

    def local_path(self, key: str) -> Path | None:
        return None

    def presigned_url(self, key: str, filename: str, expires: int = 900) -> str:
        url: str = self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{filename}"',
            },
            ExpiresIn=expires,
        )
        return url

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def check(self) -> None:
        self.client.head_bucket(Bucket=self.bucket)


def make_storage(settings: "Settings", root: Path) -> Storage:
    """The configured backend. ``root`` is the resolved local directory for 'local'."""
    if settings.storage_backend == "s3":
        storage = S3Storage(
            bucket=settings.s3_bucket,
            endpoint_url=settings.s3_endpoint_url,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            region=settings.s3_region,
        )
        storage.ensure_bucket()
        return storage
    return LocalStorage(root)
