"""Format detection and dispatch to the right reader."""

from pathlib import PurePath

from app.errors import InvalidGeoFile, UnsupportedFileType
from app.geo.archive import ArchiveLimits
from app.geo.readers.kml import read_kml, read_kmz
from app.geo.readers.shapefile import read_shapefile_zip
from app.geo.types import Dataset

SUPPORTED_EXTENSIONS = (".zip", ".kml", ".kmz")
_ZIP_MAGIC = b"PK\x03\x04"


def check_extension(filename: str) -> str:
    ext = PurePath(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFileType(
            f"Unsupported file type {ext or '(none)'!r}. "
            "Upload a .zip containing a Shapefile, a .kml or a .kmz file."
        )
    return ext


def read_dataset(filename: str, data: bytes, limits: ArchiveLimits) -> Dataset:
    ext = check_extension(filename)
    if not data:
        raise InvalidGeoFile("Uploaded file is empty.")

    # Check the content as well as the extension: a renamed file should fail with a clear error.
    is_zip = data.startswith(_ZIP_MAGIC)
    if ext in (".zip", ".kmz") and not is_zip:
        raise InvalidGeoFile(f"File has a {ext} extension but is not a zip archive.")
    if ext == ".kml" and is_zip:
        raise InvalidGeoFile("File has a .kml extension but is a zip archive (KMZ?).")

    if ext == ".zip":
        return read_shapefile_zip(data, limits)
    if ext == ".kmz":
        return read_kmz(data, limits)
    return read_kml(data)
