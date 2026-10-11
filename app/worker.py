"""Celery worker: processes uploads outside the request in ``GEO_TASK_MODE=celery``.

The API stores the raw upload in blob storage, creates the record (PROCESSING)
and enqueues a task with just the record id and the blob key. The worker runs
the same ingestion code as sync mode, so behaviour can't diverge between the two.

    celery -A app.worker worker --loglevel=info --concurrency=2

Tasks acknowledge *late*: a worker that dies mid-task leaves the message on the
queue, and another worker retries it. ``prefetch=1`` stops a worker from hoarding
long DEM jobs while others sit idle.
"""

import logging
import tempfile
from functools import lru_cache
from pathlib import Path

from celery import Celery

from app.config import get_settings, resolve_path
from app.db import Database
from app.errors import GeoAPIError
from app.models import Raster, UploadedFile
from app.services.ingestion import process_record
from app.services.rasters import process_raster_record
from app.storage import Storage, make_storage

log = logging.getLogger(__name__)
_settings = get_settings()

celery = Celery("traverse", broker=_settings.redis_url)
celery.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_time_limit=30 * 60,
    task_soft_time_limit=25 * 60,
    task_serializer="json",
    accept_content=["json"],
    task_always_eager=_settings.celery_eager,
    task_eager_propagates=False,
    broker_connection_retry_on_startup=True,
)


_registered: tuple[Database, Storage] | None = None


def register(db: Database, storage: Storage) -> None:
    """Use these resources for tasks run in this process (the API in eager mode, tests)."""
    global _registered
    _registered = (db, storage)


@lru_cache
def _from_settings() -> tuple[Database, Storage]:
    settings = get_settings()
    return Database(settings.database_url), make_storage(settings, resolve_path(settings.storage_dir))


def _get() -> tuple[Database, Storage]:
    return _registered or _from_settings()


@celery.task(name="traverse.process_file")
def process_file(file_id: str, blob_key: str) -> str:
    db, storage = _get()
    with db.session_factory() as session:
        record = session.get(UploadedFile, file_id)
        if record is None:
            log.warning("File %s vanished before processing", file_id)
            return "missing"
        try:
            process_record(session, get_settings(), record, storage.read_bytes(blob_key))
        except GeoAPIError:
            pass  # already recorded as FAILED with a message
        finally:
            storage.delete(blob_key)  # the raw upload is no longer needed
        return str(record.status)


@celery.task(name="traverse.process_raster")
def process_raster(raster_id: str, blob_key: str) -> str:
    db, storage = _get()
    with db.session_factory() as session, tempfile.TemporaryDirectory() as tmp:
        record = session.get(Raster, raster_id)
        if record is None:
            log.warning("Raster %s vanished before processing", raster_id)
            return "missing"
        local = Path(tmp) / "upload.tif"
        try:
            storage.download(blob_key, local)
            process_raster_record(session, get_settings(), storage, record, local)
        except GeoAPIError:
            pass
        finally:
            storage.delete(blob_key)
        return str(record.status)
