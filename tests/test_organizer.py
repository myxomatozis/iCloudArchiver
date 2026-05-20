from datetime import UTC, datetime
from pathlib import Path

from icloud_archiver.organizer import DownloadedFiles, organize, sidecar_dict
from icloud_archiver.types import CatalogItem


def _item(
    asset_id: str = "x",
    original_filename: str = "IMG_1234.HEIC",
    albums: list[str] | None = None,
    has_live_photo: bool = False,
) -> CatalogItem:
    return CatalogItem(
        asset_id=asset_id,
        created_at=datetime(2014, 8, 23, 15, 42, 1, tzinfo=UTC),
        size_bytes=10,
        albums=albums if albums is not None else [],
        original_filename=original_filename,
        has_live_photo=has_live_photo,
        has_edits=False,
        mime_type="image/heic",
        icloud_checksum=None,
    )


def _make_scratch(tmp_path: Path, item_id: str = "x", with_live: bool = False) -> DownloadedFiles:
    scratch = tmp_path / ".scratch"
    scratch.mkdir(exist_ok=True)
    original = scratch / f"{item_id}_orig.HEIC"
    original.write_bytes(b"original-bytes")
    if with_live:
        live = scratch / f"{item_id}_live.MOV"
        live.write_bytes(b"live-bytes")
        return DownloadedFiles(original=original, live_photo=live)
    return DownloadedFiles(original=original)


def test_no_album_falls_back_to_date_folder(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    item = _item(albums=[])
    files = _make_scratch(tmp_path)

    paths = organize(item, files, archive, sidecar=sidecar_dict(item, sha256="a" * 64, run_id="r1"))

    expected_dir = archive / "_NoAlbum" / "2014" / "08"
    assert (expected_dir / "IMG_1234.HEIC").is_file()
    assert (expected_dir / "IMG_1234.json").is_file()
    assert paths.primary == expected_dir / "IMG_1234.HEIC"
    assert paths.hardlinks == []


def test_single_album_places_under_album_folder(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    item = _item(albums=["Italy 2014"])
    files = _make_scratch(tmp_path)

    paths = organize(item, files, archive, sidecar=sidecar_dict(item, sha256="a" * 64, run_id="r1"))

    assert paths.primary == archive / "Italy 2014" / "IMG_1234.HEIC"
    assert paths.hardlinks == []
    assert (archive / "Italy 2014" / "IMG_1234.json").is_file()


def test_multi_album_creates_hardlinks_for_image_and_sidecar(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    item = _item(albums=["Family", "Highlights", "Italy 2014"])
    files = _make_scratch(tmp_path)

    paths = organize(item, files, archive, sidecar=sidecar_dict(item, sha256="a" * 64, run_id="r1"))

    primary = archive / "Family" / "IMG_1234.HEIC"
    assert paths.primary == primary
    assert (archive / "Highlights" / "IMG_1234.HEIC").is_file()
    assert (archive / "Italy 2014" / "IMG_1234.HEIC").is_file()

    primary_inode = primary.stat().st_ino
    assert (archive / "Highlights" / "IMG_1234.HEIC").stat().st_ino == primary_inode
    assert (archive / "Italy 2014" / "IMG_1234.HEIC").stat().st_ino == primary_inode

    sidecar_inode = (archive / "Family" / "IMG_1234.json").stat().st_ino
    assert (archive / "Highlights" / "IMG_1234.json").stat().st_ino == sidecar_inode
    assert (archive / "Italy 2014" / "IMG_1234.json").stat().st_ino == sidecar_inode


def test_live_photo_paired_video_placed_and_linked(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    item = _item(albums=["A", "B"], has_live_photo=True)
    files = _make_scratch(tmp_path, with_live=True)

    organize(item, files, archive, sidecar=sidecar_dict(item, sha256="a" * 64, run_id="r1"))

    primary_mov = archive / "A" / "IMG_1234_LIVE.MOV"
    assert primary_mov.is_file()
    linked_mov = archive / "B" / "IMG_1234_LIVE.MOV"
    assert linked_mov.stat().st_ino == primary_mov.stat().st_ino


def test_organize_is_idempotent(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    item = _item(albums=["A", "B"])

    files1 = _make_scratch(tmp_path, item_id="r1")
    organize(item, files1, archive, sidecar=sidecar_dict(item, sha256="a" * 64, run_id="r1"))
    primary_inode = (archive / "A" / "IMG_1234.HEIC").stat().st_ino

    files2 = _make_scratch(tmp_path, item_id="r2")
    organize(item, files2, archive, sidecar=sidecar_dict(item, sha256="a" * 64, run_id="r2"))
    assert (archive / "A" / "IMG_1234.HEIC").stat().st_ino == primary_inode


def test_distinct_assets_same_filename_same_album_do_not_collide(tmp_path: Path) -> None:
    """Two different assets sharing original_filename in the same album must each
    keep their own bytes — neither download may be silently dropped."""
    archive = tmp_path / "archive"
    archive.mkdir()
    scratch = tmp_path / ".scratch"
    scratch.mkdir()

    item_a = _item(asset_id="asset-a", original_filename="IMG_0001.HEIC", albums=["Trip"])
    src_a = scratch / "asset-a_orig.HEIC"
    src_a.write_bytes(b"AAAA-content")
    paths_a = organize(
        item_a,
        DownloadedFiles(original=src_a),
        archive,
        sidecar=sidecar_dict(item_a, sha256="a" * 64, run_id="r1"),
    )

    item_b = _item(asset_id="asset-b", original_filename="IMG_0001.HEIC", albums=["Trip"])
    src_b = scratch / "asset-b_orig.HEIC"
    src_b.write_bytes(b"BBBB-different")
    paths_b = organize(
        item_b,
        DownloadedFiles(original=src_b),
        archive,
        sidecar=sidecar_dict(item_b, sha256="b" * 64, run_id="r2"),
    )

    assert paths_a.primary != paths_b.primary
    assert paths_a.primary.read_bytes() == b"AAAA-content"
    assert paths_b.primary.read_bytes() == b"BBBB-different"


def test_distinct_assets_same_filename_shared_secondary_album_do_not_collide(
    tmp_path: Path,
) -> None:
    """A filename collision in a *secondary* (hardlinked) album must not drop data."""
    archive = tmp_path / "archive"
    archive.mkdir()
    scratch = tmp_path / ".scratch"
    scratch.mkdir()

    item_a = _item(asset_id="asset-a", original_filename="IMG_0002.HEIC", albums=["A1", "Shared"])
    src_a = scratch / "asset-a_orig.HEIC"
    src_a.write_bytes(b"AAAA")
    paths_a = organize(
        item_a,
        DownloadedFiles(original=src_a),
        archive,
        sidecar=sidecar_dict(item_a, sha256="a" * 64, run_id="r1"),
    )

    item_b = _item(asset_id="asset-b", original_filename="IMG_0002.HEIC", albums=["B1", "Shared"])
    src_b = scratch / "asset-b_orig.HEIC"
    src_b.write_bytes(b"BBBB")
    paths_b = organize(
        item_b,
        DownloadedFiles(original=src_b),
        archive,
        sidecar=sidecar_dict(item_b, sha256="b" * 64, run_id="r2"),
    )

    a_in_shared = next(p for p in paths_a.hardlinks if p.parent.name == "Shared")
    b_in_shared = next(p for p in paths_b.hardlinks if p.parent.name == "Shared")
    assert a_in_shared != b_in_shared
    assert a_in_shared.read_bytes() == b"AAAA"
    assert b_in_shared.read_bytes() == b"BBBB"


def test_sidecar_dict_shape(tmp_path: Path) -> None:
    item = _item(albums=["A", "B"], has_live_photo=True)
    side = sidecar_dict(item, sha256="a" * 64, run_id="r1")
    assert side["asset_id"] == "x"
    assert side["original_filename"] == "IMG_1234.HEIC"
    assert side["sha256"] == "a" * 64
    assert side["albums"] == ["A", "B"]
    assert side["has_live_photo"] is True
    assert "archived_at" in side
    assert side["archived_by_run"] == "r1"
