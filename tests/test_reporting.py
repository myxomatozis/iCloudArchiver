from datetime import UTC, datetime

from icloud_archiver.reporting import PlanRow, render_plan_markdown, render_run_summary


def _rows() -> list[PlanRow]:
    return [
        PlanRow(
            asset_id="a",
            created_at=datetime(2014, 1, 5, tzinfo=UTC),
            size_bytes=2_000_000_000,
            albums=["Family"],
        ),
        PlanRow(
            asset_id="b",
            created_at=datetime(2014, 1, 6, tzinfo=UTC),
            size_bytes=3_000_000_000,
            albums=["Family", "Highlights"],
        ),
    ]


def test_render_plan_markdown_summarizes_rows() -> None:
    md = render_plan_markdown(
        _rows(),
        target_bytes=4_000_000_000,
        archive_root="/Volumes/T7/iCloud-Archive",
    )
    assert "# iCloud Archiver — Plan" in md
    assert "2 items" in md
    assert "5.0 GB" in md
    assert "2014-01-05" in md
    assert "2014-01-06" in md
    assert "Family" in md
    assert "Highlights" in md
    assert "/Volumes/T7/iCloud-Archive" in md
    # All rows have albums, so the no-album note must NOT appear
    assert "not in any album" not in md


def test_render_plan_markdown_notes_photos_without_album() -> None:
    rows = [
        PlanRow(
            asset_id="a",
            created_at=datetime(2020, 6, 1, tzinfo=UTC),
            size_bytes=1_000_000_000,
            albums=["Holidays"],
        ),
        PlanRow(
            asset_id="b",
            created_at=datetime(2020, 6, 2, tzinfo=UTC),
            size_bytes=1_000_000_000,
            albums=[],  # no album — will fall back to date folder
        ),
        PlanRow(
            asset_id="c",
            created_at=datetime(2020, 6, 3, tzinfo=UTC),
            size_bytes=1_000_000_000,
            albums=[],
        ),
    ]
    md = render_plan_markdown(rows, target_bytes=3_000_000_000, archive_root="/arc")
    assert "Holidays" in md
    assert "2 photos not in any album" in md
    assert "YYYY/MM/DD" in md


def test_render_run_summary_includes_failure_counts() -> None:
    out = render_run_summary(
        archived=10,
        deleted=9,
        failed_verify=1,
        failed_download=0,
        skipped=0,
        bytes_archived=12_000_000_000,
        bytes_pending_free=10_000_000_000,
    )
    assert "10 archived" in out
    assert "9 deleted" in out
    assert "1 failed verify" in out
    assert "10.0 GB" in out
    assert "Recently Deleted" in out
