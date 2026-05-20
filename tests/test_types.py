from datetime import UTC, datetime

import pytest

from icloud_archiver.types import CatalogItem, ItemState, RunStatus


def test_catalog_item_is_frozen():
    item = CatalogItem(
        asset_id="abc123",
        created_at=datetime(2014, 8, 23, 15, 42, 1, tzinfo=UTC),
        size_bytes=4_823_942,
        albums=["Family/Italy 2014", "Highlights"],
        original_filename="IMG_1234.HEIC",
        has_live_photo=True,
        has_edits=False,
        mime_type="image/heic",
        icloud_checksum=None,
    )
    with pytest.raises(AttributeError):
        item.asset_id = "different"  # type: ignore[misc]


def test_item_state_terminal_set():
    expected_terminal = {
        ItemState.DELETED,
        ItemState.SKIPPED,
        ItemState.FAILED_VERIFY,
        ItemState.FAILED_DOWNLOAD,
    }
    for state in ItemState:
        assert state.is_terminal() == (state in expected_terminal), state


def test_run_status_values():
    assert RunStatus.COMPLETED.value == "completed"
    assert RunStatus.ABORTED.value == "aborted"
    assert RunStatus.CRASHED.value == "crashed"
