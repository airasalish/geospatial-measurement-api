"""Defensive access to uploaded zip archives (shapefile bundles, KMZ).

Nothing is extracted to disk. Members are read into memory with hard limits, which
rules out zip-slip path traversal and caps the damage a zip bomb can do. Declared
sizes in the zip header are not trusted: reads are capped as they stream.
"""

import io
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.errors import InvalidGeoFile


@dataclass(frozen=True)
class ArchiveLimits:
    max_members: int
    max_uncompressed_bytes: int
    max_compression_ratio: int


def _is_junk(name: str) -> bool:
    """macOS resource forks and hidden files that zip tools add on their own."""
    path = PurePosixPath(name)
    return name.startswith("__MACOSX/") or any(p.startswith(".") for p in path.parts)


class SafeZip:
    def __init__(self, data: bytes, limits: ArchiveLimits) -> None:
        try:
            self._zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise InvalidGeoFile("File is not a valid zip archive.") from exc

        self._limits = limits
        self._budget = limits.max_uncompressed_bytes
        infos = [i for i in self._zf.infolist() if not i.is_dir() and not _is_junk(i.filename)]

        if len(infos) > limits.max_members:
            raise InvalidGeoFile(f"Archive has too many files (limit {limits.max_members}).")
        if any(i.flag_bits & 0x1 for i in infos):
            raise InvalidGeoFile("Encrypted archives are not supported.")
        declared = sum(i.file_size for i in infos)
        if declared > limits.max_uncompressed_bytes:
            raise InvalidGeoFile("Archive is too large once uncompressed.")
        compressed = sum(i.compress_size for i in infos) or 1
        if declared / compressed > limits.max_compression_ratio:
            raise InvalidGeoFile("Archive compression ratio is suspiciously high.")

        self.members: dict[str, zipfile.ZipInfo] = {i.filename: i for i in infos}

    def read(self, name: str) -> bytes:
        """Read one member, enforcing the shared uncompressed-size budget."""
        info = self.members[name]
        try:
            with self._zf.open(info) as fh:
                # Read one byte past the budget so an archive that lies about sizes is caught.
                chunk = fh.read(self._budget + 1)
        except (zipfile.BadZipFile, EOFError, OSError) as exc:
            raise InvalidGeoFile(f"Archive member {name!r} is corrupt.") from exc
        if len(chunk) > self._budget:
            raise InvalidGeoFile("Archive is too large once uncompressed.")
        self._budget -= len(chunk)
        return chunk
