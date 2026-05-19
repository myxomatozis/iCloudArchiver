from datetime import UTC, datetime

import pytest

from icloud_archiver.deleter import DeleteError, delete_asset
from icloud_archiver.types import CatalogItem
from tests.fakes import FakeAsset, FakeICloudPhotos


def _item() -> CatalogItem:
    return CatalogItem(
        asset_id="a",
        created_at=datetime(2015, 1, 1, tzinfo=UTC),
        size_bytes=10,
        albums=[],
        original_filename="a.HEIC",
        has_live_photo=False,
        has_edits=False,
        mime_type="image/heic",
        icloud_checksum=None,
    )


def test_delete_removes_from_icloud() -> None:
    item = _item()
    fake = FakeICloudPhotos(assets=[FakeAsset(item=item, original_bytes=b"x")])
    delete_asset(item, fake)
    assert list(fake.iter_oldest_first()) == []


def test_delete_already_gone_is_treated_as_success() -> None:
    """Re-deleting an already-deleted item should not raise (idempotency)."""
    item = _item()
    fake = FakeICloudPhotos(assets=[FakeAsset(item=item, original_bytes=b"x")])
    delete_asset(item, fake)
    delete_asset(item, fake)  # second call should not raise


def test_delete_failure_propagates_with_detail() -> None:
    item = _item()
    fake = FakeICloudPhotos(assets=[FakeAsset(item=item, original_bytes=b"x")])
    fake.fail_delete_for.add("a")
    with pytest.raises(DeleteError, match="a"):
        delete_asset(item, fake)
