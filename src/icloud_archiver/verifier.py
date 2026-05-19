"""Strict per-file verification: size + parse + sha256 + optional checksum compare."""

import hashlib
import struct
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from icloud_archiver.types import CatalogItem

_IMAGE_MIMES = frozenset({
    "image/jpeg",
    "image/png",
    "image/heic",
    "image/heif",
    "image/tiff",
    "image/gif",
})

_VIDEO_MIMES = frozenset({"video/mp4", "video/quicktime", "video/mov"})


class VerifyError(Exception):
    """Raised when a verification step fails."""


@dataclass(frozen=True)
class VerifyResult:
    sha256: str


def verify_size(path: Path, *, expected: int) -> None:
    actual = path.stat().st_size
    if actual != expected:
        raise VerifyError(f"size mismatch for {path.name}: expected {expected}, got {actual}")


def verify_parse(path: Path, *, mime_type: str) -> None:
    mime = (mime_type or "").lower()
    if mime in _IMAGE_MIMES:
        try:
            with Image.open(path) as img:
                img.verify()
        except (UnidentifiedImageError, OSError) as exc:
            raise VerifyError(f"image parse failed for {path.name}: {exc}") from exc
        return
    if mime in _VIDEO_MIMES:
        _walk_mp4_atoms(path)
        return
    # Unknown / generic: at minimum, confirm non-empty.
    if path.stat().st_size == 0:
        raise VerifyError(f"empty file: {path.name}")


def _walk_mp4_atoms(path: Path) -> None:
    seen: set[str] = set()
    total = 0
    file_size = path.stat().st_size
    with path.open("rb") as f:
        while True:
            header = f.read(8)
            if not header:
                break
            if len(header) < 8:
                raise VerifyError(f"mp4 truncated header in {path.name}")
            size, box_type = struct.unpack(">I4s", header)
            if size == 1:
                ext = f.read(8)
                if len(ext) < 8:
                    raise VerifyError(f"mp4 truncated extended size in {path.name}")
                size = struct.unpack(">Q", ext)[0]
                header_len = 16
            else:
                header_len = 8
            if size < header_len:
                raise VerifyError(f"mp4 invalid box size {size} in {path.name}")
            seen.add(box_type.decode("ascii", errors="replace"))
            total += size
            f.seek(size - header_len, 1)
    if "ftyp" not in seen:
        raise VerifyError(f"mp4 missing ftyp atom in {path.name}")
    if "moov" not in seen:
        raise VerifyError(f"mp4 missing moov atom in {path.name}")
    if "mdat" not in seen:
        raise VerifyError(f"mp4 missing mdat atom in {path.name}")
    if total != file_size:
        raise VerifyError(
            f"mp4 atoms ({total}B) do not cover file ({file_size}B) in {path.name}"
        )


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(item: CatalogItem, path: Path) -> VerifyResult:
    """Run the full chain: size → parse → sha → optional checksum compare."""
    verify_size(path, expected=item.size_bytes)
    verify_parse(path, mime_type=item.mime_type)
    digest = sha256_of(path)
    if item.icloud_checksum and item.icloud_checksum.lower() != digest.lower():
        raise VerifyError(
            f"checksum mismatch for {item.asset_id}: "
            f"iCloud reported {item.icloud_checksum}, local sha256 {digest}"
        )
    return VerifyResult(sha256=digest)
