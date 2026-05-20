from datetime import UTC, datetime
from pathlib import Path

import pytest

from icloud_archiver.types import CatalogItem
from icloud_archiver.verifier import (
    VerifyError,
    sha256_of,
    verify,
    verify_parse,
    verify_size,
)
from tests.fixtures import (
    make_broken_mp4,
    make_eof_box_mp4,
    make_jpeg,
    make_minimal_mp4,
    make_png,
    truncate_file,
)


def _item(size_bytes: int, mime: str = "image/jpeg", checksum: str | None = None) -> CatalogItem:
    return CatalogItem(
        asset_id="x",
        created_at=datetime(2015, 1, 1, tzinfo=UTC),
        size_bytes=size_bytes,
        albums=[],
        original_filename="x.jpg",
        has_live_photo=False,
        has_edits=False,
        mime_type=mime,
        icloud_checksum=checksum,
    )


def test_verify_size_passes_when_match(tmp_path: Path) -> None:
    p = make_jpeg(tmp_path / "a.jpg")
    verify_size(p, expected=p.stat().st_size)


def test_verify_size_fails_when_mismatch(tmp_path: Path) -> None:
    p = make_jpeg(tmp_path / "a.jpg")
    with pytest.raises(VerifyError):
        verify_size(p, expected=p.stat().st_size - 1)


def test_verify_parse_jpeg_passes(tmp_path: Path) -> None:
    p = make_jpeg(tmp_path / "a.jpg")
    verify_parse(p, mime_type="image/jpeg")


def test_verify_parse_jpeg_truncated_fails(tmp_path: Path) -> None:
    p = make_jpeg(tmp_path / "a.jpg")
    truncate_file(p, keep_bytes=16)
    with pytest.raises(VerifyError):
        verify_parse(p, mime_type="image/jpeg")


def test_verify_parse_png_passes(tmp_path: Path) -> None:
    p = make_png(tmp_path / "a.png")
    verify_parse(p, mime_type="image/png")


def test_verify_parse_mp4_passes(tmp_path: Path) -> None:
    p = make_minimal_mp4(tmp_path / "a.mp4")
    verify_parse(p, mime_type="video/mp4")


def test_verify_parse_mp4_eof_box_passes(tmp_path: Path) -> None:
    """A box with size==0 extends to EOF (ISO 14496-12) and must be accepted."""
    p = make_eof_box_mp4(tmp_path / "a.mp4")
    verify_parse(p, mime_type="video/mp4")


def test_verify_parse_mp4_missing_moov_fails(tmp_path: Path) -> None:
    p = make_broken_mp4(tmp_path / "a.mp4")
    with pytest.raises(VerifyError, match="moov"):
        verify_parse(p, mime_type="video/mp4")


def test_sha256_of_is_stable(tmp_path: Path) -> None:
    p = make_jpeg(tmp_path / "a.jpg")
    a = sha256_of(p)
    b = sha256_of(p)
    assert a == b
    assert len(a) == 64


def test_verify_full_chain_passes(tmp_path: Path) -> None:
    p = make_jpeg(tmp_path / "a.jpg")
    item = _item(size_bytes=p.stat().st_size, mime="image/jpeg")
    result = verify(item, p)
    assert result.sha256 == sha256_of(p)


def test_verify_full_chain_checksum_mismatch_fails(tmp_path: Path) -> None:
    p = make_jpeg(tmp_path / "a.jpg")
    item = _item(size_bytes=p.stat().st_size, mime="image/jpeg", checksum="deadbeef" * 8)
    with pytest.raises(VerifyError, match="checksum"):
        verify(item, p)
