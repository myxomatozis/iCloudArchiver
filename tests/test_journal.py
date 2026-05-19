from datetime import UTC, datetime
from pathlib import Path

from icloud_archiver.journal import Journal
from icloud_archiver.types import CatalogItem, ItemState, RunStatus


def _make_item(asset_id: str = "asset_1", size: int = 1000) -> CatalogItem:
    return CatalogItem(
        asset_id=asset_id,
        created_at=datetime(2015, 1, 1, tzinfo=UTC),
        size_bytes=size,
        albums=["Album A"],
        original_filename=f"{asset_id}.HEIC",
        has_live_photo=False,
        has_edits=False,
        mime_type="image/heic",
        icloud_checksum=None,
    )


def test_open_creates_schema(tmp_path: Path) -> None:
    journal = Journal.open(tmp_path / "state.db")
    assert (tmp_path / "state.db").exists()
    journal.close()


def test_start_and_end_run(tmp_path: Path) -> None:
    journal = Journal.open(tmp_path / "state.db")
    run_id = journal.start_run(target_bytes=1_000_000, dry_run=False, archive_root="/Volumes/X")
    assert run_id  # ULID is non-empty
    journal.end_run(run_id, RunStatus.COMPLETED)
    rows = journal.list_runs()
    assert len(rows) == 1
    assert rows[0]["run_id"] == run_id
    assert rows[0]["ended_status"] == "completed"


def test_transition_records_event_and_updates_state(tmp_path: Path) -> None:
    journal = Journal.open(tmp_path / "state.db")
    run_id = journal.start_run(target_bytes=1_000_000, dry_run=False, archive_root="/Volumes/X")
    item = _make_item()

    journal.upsert_item(item, run_id, ItemState.PLANNED)
    assert journal.get_state(item.asset_id) == ItemState.PLANNED

    journal.transition(item.asset_id, ItemState.DOWNLOADING, run_id=run_id)
    journal.transition(item.asset_id, ItemState.DOWNLOADED, run_id=run_id)
    assert journal.get_state(item.asset_id) == ItemState.DOWNLOADED

    events = journal.events_for(item.asset_id)
    assert [e["to_state"] for e in events] == ["PLANNED", "DOWNLOADING", "DOWNLOADED"]


def test_resumable_items_returns_only_non_terminal(tmp_path: Path) -> None:
    journal = Journal.open(tmp_path / "state.db")
    run_id = journal.start_run(target_bytes=1_000_000, dry_run=False, archive_root="/Volumes/X")
    a, b, c = _make_item("a"), _make_item("b"), _make_item("c")
    journal.upsert_item(a, run_id, ItemState.PLANNED)
    journal.upsert_item(b, run_id, ItemState.DELETED)
    journal.upsert_item(c, run_id, ItemState.FAILED_VERIFY)

    resumable = journal.resumable_items()
    assert [r.asset_id for r in resumable] == ["a"]


def test_bytes_freed_total(tmp_path: Path) -> None:
    journal = Journal.open(tmp_path / "state.db")
    run_id = journal.start_run(target_bytes=10_000, dry_run=False, archive_root="/Volumes/X")
    a = _make_item("a", size=3_000)
    b = _make_item("b", size=4_000)
    c = _make_item("c", size=5_000)
    for item in (a, b, c):
        journal.upsert_item(item, run_id, ItemState.PLANNED)
    journal.transition("a", ItemState.DELETED, run_id=run_id)
    journal.transition("b", ItemState.DELETED, run_id=run_id)
    # c not deleted yet

    assert journal.bytes_freed_total(run_id) == 7_000


def test_is_terminal_reflects_state(tmp_path: Path) -> None:
    """is_terminal returns True only when the item's state is in the terminal set."""
    journal = Journal.open(tmp_path / "state.db")
    run_id = journal.start_run(target_bytes=10_000, dry_run=False, archive_root="/Volumes/X")
    a, b, c = _make_item("a"), _make_item("b"), _make_item("c")
    journal.upsert_item(a, run_id, ItemState.PLANNED)        # non-terminal
    journal.upsert_item(b, run_id, ItemState.DELETED)        # terminal
    journal.upsert_item(c, run_id, ItemState.FAILED_VERIFY)  # terminal
    assert journal.is_terminal("b")
    assert journal.is_terminal("c")
    assert not journal.is_terminal("a")
    assert not journal.is_terminal("never-seen")  # unknown items aren't terminal
