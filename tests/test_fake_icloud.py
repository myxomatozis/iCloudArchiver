from datetime import UTC, datetime
from pathlib import Path

import pytest

from icloud_archiver.types import CatalogItem
from tests.fakes import FakeAsset, FakeICloudPhotos


def _asset(asset_id: str, day: int, size: int = 1000, albums: list[str] | None = None) -> FakeAsset:
    return FakeAsset(
        item=CatalogItem(
            asset_id=asset_id,
            created_at=datetime(2014, 1, day, tzinfo=UTC),
            size_bytes=size,
            albums=albums or [],
            original_filename=f"{asset_id}.HEIC",
            has_live_photo=False,
            has_edits=False,
            mime_type="image/heic",
            icloud_checksum=None,
        ),
        original_bytes=b"X" * size,
    )


def test_fake_yields_oldest_first(tmp_path: Path) -> None:
    fake = FakeICloudPhotos(assets=[_asset("c", 3), _asset("a", 1), _asset("b", 2)])
    ids = [item.asset_id for item in fake.iter_oldest_first()]
    assert ids == ["a", "b", "c"]


def test_fake_download_writes_bytes(tmp_path: Path) -> None:
    asset = _asset("a", 1, size=2048)
    fake = FakeICloudPhotos(assets=[asset])
    dest = tmp_path / "out.HEIC"
    fake.download_original(asset.item.asset_id, dest)
    assert dest.read_bytes() == b"X" * 2048


def test_fake_delete_removes_from_iteration(tmp_path: Path) -> None:
    fake = FakeICloudPhotos(assets=[_asset("a", 1), _asset("b", 2)])
    fake.delete("a")
    assert [i.asset_id for i in fake.iter_oldest_first()] == ["b"]


def test_fake_delete_unknown_raises(tmp_path: Path) -> None:
    fake = FakeICloudPhotos(assets=[_asset("a", 1)])
    with pytest.raises(KeyError):
        fake.delete("nope")


def test_fake_can_simulate_download_failure(tmp_path: Path) -> None:
    asset = _asset("a", 1)
    fake = FakeICloudPhotos(assets=[asset])
    fake.fail_download_for.add("a")
    dest = tmp_path / "out.HEIC"
    with pytest.raises(OSError, match="injected download failure"):
        fake.download_original("a", dest)


def test_fake_can_serve_truncated_bytes(tmp_path: Path) -> None:
    asset = _asset("a", 1, size=2048)
    fake = FakeICloudPhotos(assets=[asset])
    fake.truncate_download_for["a"] = 100
    dest = tmp_path / "out.HEIC"
    fake.download_original("a", dest)
    assert dest.stat().st_size == 100
