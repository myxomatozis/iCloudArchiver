import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from icloud_archiver.downloader import DownloadError, fetch_item
from icloud_archiver.types import CatalogItem
from tests.fakes import FakeAsset, FakeICloudPhotos


def _item(asset_id: str = "a", has_live: bool = False, has_edits: bool = False) -> CatalogItem:
    return CatalogItem(
        asset_id=asset_id,
        created_at=datetime(2015, 1, 1, tzinfo=UTC),
        size_bytes=1000,
        albums=[],
        original_filename=f"{asset_id}.HEIC",
        has_live_photo=has_live,
        has_edits=has_edits,
        mime_type="image/heic",
        icloud_checksum=None,
    )


def _fake_with(
    item: CatalogItem, *, with_live: bool = False, with_edits: bool = False
) -> FakeICloudPhotos:
    asset = FakeAsset(
        item=item,
        original_bytes=b"X" * item.size_bytes,
        live_photo_bytes=b"LIVE" * 100 if with_live else None,
        edited_bytes=b"EDIT" * 100 if with_edits else None,
    )
    return FakeICloudPhotos(assets=[asset])


def test_fetch_item_writes_original_to_scratch(tmp_path: Path) -> None:
    item = _item()
    fake = _fake_with(item)
    files = fetch_item(item, fake, scratch_dir=tmp_path)
    assert files.original.read_bytes() == b"X" * 1000
    assert files.original.suffix == ".HEIC"
    assert files.live_photo is None
    assert files.edited is None


def test_fetch_item_includes_live_photo(tmp_path: Path) -> None:
    item = _item(has_live=True)
    fake = _fake_with(item, with_live=True)
    files = fetch_item(item, fake, scratch_dir=tmp_path)
    assert files.live_photo is not None
    assert files.live_photo.suffix == ".MOV"


def test_fetch_item_includes_edited(tmp_path: Path) -> None:
    item = _item(has_edits=True)
    fake = _fake_with(item, with_edits=True)
    files = fetch_item(item, fake, scratch_dir=tmp_path)
    assert files.edited is not None


def test_fetch_item_fsyncs_each_download_before_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Original, live photo and edited file are each fsynced for crash durability."""
    item = _item(has_live=True, has_edits=True)
    fake = _fake_with(item, with_live=True, with_edits=True)

    fsync_calls: list[int] = []
    monkeypatch.setattr(os, "fsync", fsync_calls.append)

    fetch_item(item, fake, scratch_dir=tmp_path)

    assert len(fsync_calls) == 3


def test_fetch_item_raises_on_download_failure(tmp_path: Path) -> None:
    item = _item()
    fake = _fake_with(item)
    fake.fail_download_for.add("a")
    with pytest.raises(DownloadError):
        fetch_item(item, fake, scratch_dir=tmp_path)
    # Scratch should be cleaned up
    assert not any(tmp_path.iterdir())
