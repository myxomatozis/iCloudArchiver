import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

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
    import sqlite3

    conn = sqlite3.connect(str(tmp_path / "state.db"))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"runs", "items", "item_events"} <= tables
    conn.close()
    journal.close()


def test_context_manager_closes_connection(tmp_path: Path) -> None:
    """`with Journal.open(...)` closes the SQLite connection on exit."""
    with Journal.open(tmp_path / "state.db") as journal:
        journal.start_run(target_bytes=1, dry_run=False, archive_root="/x")
    with pytest.raises(sqlite3.ProgrammingError):
        journal._conn.execute("SELECT 1")


def test_start_and_end_run(tmp_path: Path) -> None:
    journal = Journal.open(tmp_path / "state.db")
    run_id = journal.start_run(target_bytes=1_000_000, dry_run=False, archive_root="/Volumes/X")
    assert run_id  # ULID is non-empty
    journal.end_run(run_id, RunStatus.COMPLETED)
    rows = journal.list_runs()
    assert len(rows) == 1
    assert rows[0]["run_id"] == run_id
    assert rows[0]["ended_status"] == "completed"
    journal.close()


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
    journal.close()


def test_get_primary_path_returns_stored_path(tmp_path: Path) -> None:
    """get_primary_path returns the archived file path, or None before it is set."""
    journal = Journal.open(tmp_path / "state.db")
    run_id = journal.start_run(target_bytes=1, dry_run=False, archive_root="/x")
    item = _make_item("a")
    journal.upsert_item(item, run_id, ItemState.PLANNED)
    assert journal.get_primary_path("a") is None
    assert journal.get_primary_path("never-seen") is None

    journal.transition("a", ItemState.ARCHIVED, run_id=run_id, primary_path="/archive/a.HEIC")
    assert journal.get_primary_path("a") == "/archive/a.HEIC"
    journal.close()


def test_resumable_items_returns_only_non_terminal(tmp_path: Path) -> None:
    journal = Journal.open(tmp_path / "state.db")
    run_id = journal.start_run(target_bytes=1_000_000, dry_run=False, archive_root="/Volumes/X")
    a, b, c = _make_item("a"), _make_item("b"), _make_item("c")
    journal.upsert_item(a, run_id, ItemState.PLANNED)
    journal.upsert_item(b, run_id, ItemState.DELETED)
    journal.upsert_item(c, run_id, ItemState.FAILED_VERIFY)

    resumable = journal.resumable_items()
    assert [r.asset_id for r in resumable] == ["a"]
    journal.close()


def test_reset_items_removes_rows_and_events(tmp_path: Path) -> None:
    """reset_items deletes item rows (and their events) so the assets are
    treated as new again; it returns the count actually removed."""
    journal = Journal.open(tmp_path / "state.db")
    run_id = journal.start_run(target_bytes=10_000, dry_run=False, archive_root="/Volumes/X")
    a, b = _make_item("a"), _make_item("b")
    journal.upsert_item(a, run_id, ItemState.PLANNED)
    journal.upsert_item(b, run_id, ItemState.PLANNED)
    journal.transition("a", ItemState.DELETED, run_id=run_id)

    removed = journal.reset_items(["a", "never-seen"])

    assert removed == 1
    assert journal.get_state("a") is None
    assert journal.events_for("a") == []
    assert journal.get_state("b") == ItemState.PLANNED
    journal.close()


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

    # All-time aggregate (no run_id) covers the IS NULL branch too
    assert journal.bytes_freed_total() == 7_000
    assert journal.bytes_freed_total(run_id) == 7_000
    journal.close()


def test_is_terminal_reflects_state(tmp_path: Path) -> None:
    """is_terminal returns True only when the item's state is in the terminal set."""
    journal = Journal.open(tmp_path / "state.db")
    run_id = journal.start_run(target_bytes=10_000, dry_run=False, archive_root="/Volumes/X")
    a, b, c = _make_item("a"), _make_item("b"), _make_item("c")
    journal.upsert_item(a, run_id, ItemState.PLANNED)  # non-terminal
    journal.upsert_item(b, run_id, ItemState.DELETED)  # terminal
    journal.upsert_item(c, run_id, ItemState.FAILED_VERIFY)  # terminal
    assert journal.is_terminal("b")
    assert journal.is_terminal("c")
    assert not journal.is_terminal("a")
    assert not journal.is_terminal("never-seen")  # unknown items aren't terminal
    journal.close()


def test_upsert_item_stores_full_catalog_item(tmp_path: Path) -> None:
    """upsert_item stores all CatalogItem fields; a direct SQL read-back must reflect them."""
    import json as _json
    import sqlite3

    journal = Journal.open(tmp_path / "state.db")
    run_id = journal.start_run(target_bytes=1_000, dry_run=True, archive_root="/Volumes/X")
    item = CatalogItem(
        asset_id="full_item",
        created_at=datetime(2020, 6, 15, 12, 0, 0, tzinfo=UTC),
        size_bytes=4_242,
        albums=["Holidays", "Family"],
        original_filename="photo.HEIC",
        has_live_photo=True,
        has_edits=False,
        mime_type="image/heic",
        icloud_checksum="abc123",
    )
    journal.upsert_item(item, run_id, ItemState.PLANNED)
    journal.close()

    # Read back via a raw connection to verify the stored values directly.
    conn = sqlite3.connect(str(tmp_path / "state.db"))
    row = conn.execute(
        "SELECT original_filename, albums, has_live_photo, has_edits, mime_type "
        "FROM items WHERE asset_id = ?",
        (item.asset_id,),
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == "photo.HEIC"
    assert _json.loads(row[1]) == ["Holidays", "Family"]
    assert row[2] == 1  # has_live_photo stored as integer 1
    assert row[3] == 0  # has_edits stored as integer 0
    assert row[4] == "image/heic"


def test_items_for_run(tmp_path: Path) -> None:
    """items_for_run returns fully populated CatalogItems for a given plan run.

    Verifies:
    - Items from the correct run in PLANNED state are returned.
    - Items in terminal states are excluded.
    - Items from a different run are excluded.
    """
    journal = Journal.open(tmp_path / "state.db")

    plan_run = journal.start_run(target_bytes=1_000, dry_run=True, archive_root="/Volumes/X")
    other_run = journal.start_run(target_bytes=1_000, dry_run=False, archive_root="/Volumes/X")

    planned_item = CatalogItem(
        asset_id="planned",
        created_at=datetime(2019, 3, 10, tzinfo=UTC),
        size_bytes=2_000,
        albums=["Trips", "Italy"],
        original_filename="venice.HEIC",
        has_live_photo=True,
        has_edits=True,
        mime_type="image/heic",
        icloud_checksum=None,
    )
    terminal_item = CatalogItem(
        asset_id="terminal",
        created_at=datetime(2019, 4, 1, tzinfo=UTC),
        size_bytes=1_000,
        albums=[],
        original_filename="gone.jpg",
        has_live_photo=False,
        has_edits=False,
        mime_type="image/jpeg",
        icloud_checksum=None,
    )
    other_run_item = CatalogItem(
        asset_id="other_run",
        created_at=datetime(2019, 5, 1, tzinfo=UTC),
        size_bytes=500,
        albums=["Work"],
        original_filename="work.png",
        has_live_photo=False,
        has_edits=False,
        mime_type="image/png",
        icloud_checksum=None,
    )

    journal.upsert_item(planned_item, plan_run, ItemState.PLANNED)
    journal.upsert_item(terminal_item, plan_run, ItemState.PLANNED)
    journal.transition("terminal", ItemState.DELETED, run_id=plan_run)
    journal.upsert_item(other_run_item, other_run, ItemState.PLANNED)
    journal.end_run(plan_run, RunStatus.COMPLETED)
    journal.end_run(other_run, RunStatus.COMPLETED)

    results = journal.items_for_run(plan_run)
    journal.close()

    # Only 'planned' should be returned — terminal and other-run items excluded.
    assert len(results) == 1
    result = results[0]
    assert result.asset_id == "planned"
    assert result.albums == ["Trips", "Italy"]
    assert result.original_filename == "venice.HEIC"
    assert result.has_live_photo is True
    assert result.has_edits is True
    assert result.mime_type == "image/heic"
    assert result.size_bytes == 2_000
    assert result.created_at == datetime(2019, 3, 10, tzinfo=UTC)


def test_items_for_run_finds_items_re_planned_by_a_later_run(tmp_path: Path) -> None:
    """Regression: when an item was first seen by an earlier plan and a later
    plan re-upserts it as PLANNED, items_for_run(later_run) must still find
    it. `first_seen_run` is set on INSERT and never updated, so a filter on
    that column would miss the re-planned item — the correct match is via
    the item_events row written by the later plan."""
    journal = Journal.open(tmp_path / "state.db")

    first_plan = journal.start_run(target_bytes=1, dry_run=True, archive_root="/x")
    second_plan = journal.start_run(target_bytes=1, dry_run=True, archive_root="/x")
    item = _make_item("a")
    journal.upsert_item(item, first_plan, ItemState.PLANNED)
    journal.end_run(first_plan, RunStatus.COMPLETED)
    # Second plan re-yields the same item and re-upserts it as PLANNED.
    journal.upsert_item(item, second_plan, ItemState.PLANNED)
    journal.end_run(second_plan, RunStatus.COMPLETED)

    results = journal.items_for_run(second_plan)
    journal.close()

    assert [r.asset_id for r in results] == ["a"]
