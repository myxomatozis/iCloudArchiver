"""Synthetic fixture generators for tests that need real image/video bytes."""

import struct
from pathlib import Path

from PIL import Image


def make_jpeg(
    path: Path,
    width: int = 64,
    height: int = 64,
    color: tuple[int, int, int] = (200, 100, 50),
) -> Path:
    img = Image.new("RGB", (width, height), color=color)
    img.save(path, format="JPEG", quality=80)
    return path


def make_png(path: Path, width: int = 64, height: int = 64) -> Path:
    Image.new("RGB", (width, height), color=(0, 200, 0)).save(path, format="PNG")
    return path


def truncate_file(path: Path, keep_bytes: int) -> Path:
    data = path.read_bytes()
    path.write_bytes(data[:keep_bytes])
    return path


def _atom(box_type: bytes, payload: bytes) -> bytes:
    size = 8 + len(payload)
    return struct.pack(">I", size) + box_type + payload


def make_minimal_mp4(path: Path) -> Path:
    """A minimal MP4 with ftyp + moov + mdat top-level atoms."""
    ftyp = _atom(b"ftyp", b"isom\x00\x00\x02\x00" + b"isomiso2avc1mp41")
    moov = _atom(b"moov", b"")  # empty but present
    mdat = _atom(b"mdat", b"\x00" * 64)
    path.write_bytes(ftyp + moov + mdat)
    return path


def make_broken_mp4(path: Path) -> Path:
    """An MP4-looking file missing the moov atom."""
    ftyp = _atom(b"ftyp", b"isom\x00\x00\x02\x00" + b"isomiso2avc1mp41")
    mdat = _atom(b"mdat", b"\x00" * 64)
    path.write_bytes(ftyp + mdat)  # no moov
    return path


def make_eof_box_mp4(path: Path) -> Path:
    """A valid MP4 whose final mdat box uses size==0 (extends to EOF per ISO 14496-12)."""
    ftyp = _atom(b"ftyp", b"isom\x00\x00\x02\x00" + b"isomiso2avc1mp41")
    moov = _atom(b"moov", b"")
    mdat = struct.pack(">I", 0) + b"mdat" + b"\x00" * 64
    path.write_bytes(ftyp + moov + mdat)
    return path
