from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from icloud_archiver.journal import Journal
from icloud_archiver.selector import select_until
from icloud_archiver.types import CatalogItem, ItemState


def _items(count: int, size: int = 1_000) -> list[CatalogItem]:
    base = datetime(2014, 1, 1, tzinfo=UTC)
    return [
        CatalogItem(
            asset_id=f"a{i:03d}",
            created_at=base + timedelta(days=i),
            size_bytes=size,
            albums=[],
            original_filename=f"a{i:03d}.HEIC",
            has_live_photo=False,
            has_edits=False,
            mime_type="image/heic",
            icloud_checksum=None,
        )
        for i in range(count)
    ]


def _journal(tmp_path: Path) -> Journal:
    return Journal.open(tmp_path / "state.db")


def test_select_until_stops_at_target(tmp_path: Path) -> None:
    items = _items(10, size=1_000)  # total 10 KB
    selected = list(select_until(items, target_bytes=3_500, journal=_journal(tmp_path)))
    assert [s.asset_id for s in selected] == ["a000", "a001", "a002", "a003"]
    assert sum(s.size_bytes for s in selected) >= 3_500


def test_select_until_empty_target_returns_empty(tmp_path: Path) -> None:
    items = _items(5)
    selected = list(select_until(items, target_bytes=0, journal=_journal(tmp_path)))
    assert selected == []


def test_select_until_target_exceeds_catalog(tmp_path: Path) -> None:
    items = _items(3, size=1_000)
    selected = list(select_until(items, target_bytes=1_000_000_000, journal=_journal(tmp_path)))
    assert [s.asset_id for s in selected] == ["a000", "a001", "a002"]


def test_select_until_skips_only_terminal_items(tmp_path: Path) -> None:
    """Terminal-state items are skipped; non-terminal known items are re-yielded
    (so a crashed prior run resumes through the idempotent pipeline)."""
    items = _items(5, size=1_000)
    journal = _journal(tmp_path)
    run_id = journal.start_run(target_bytes=10_000, dry_run=False, archive_root="/X")
    # a001 reached terminal DELETED → skip
    # a003 is PLANNED (crashed mid-run previously) → re-include
    journal.upsert_item(items[1], run_id, ItemState.DELETED)
    journal.upsert_item(items[3], run_id, ItemState.PLANNED)

    selected = list(select_until(items, target_bytes=3_500, journal=journal))
    # a001 skipped (terminal), a003 included (non-terminal — resume)
    assert [s.asset_id for s in selected] == ["a000", "a002", "a003", "a004"]


def test_select_until_does_not_consume_iterator_past_target(tmp_path: Path) -> None:
    """Iterator should be lazy: items past the cutoff are never read."""
    consumed: list[str] = []

    def lazy_iter() -> Iterable[CatalogItem]:
        for i in _items(100, size=1_000):
            consumed.append(i.asset_id)
            yield i

    list(select_until(lazy_iter(), target_bytes=2_500, journal=_journal(tmp_path)))
    # Cumulative >= 2500 after 3 items; one extra may be consumed before we exit.
    assert len(consumed) <= 4
