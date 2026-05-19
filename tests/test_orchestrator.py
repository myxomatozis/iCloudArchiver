from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from icloud_archiver import orchestrator as orch
from icloud_archiver.journal import Journal
from icloud_archiver.orchestrator import InsufficientSpace, run_archival
from icloud_archiver.types import CatalogItem, ItemState, RunStatus
from tests.fakes import FakeAsset, FakeICloudPhotos
from tests.fixtures import make_jpeg


def _assets(count: int, size: int = 1000, with_live: bool = False) -> list[FakeAsset]:
    base = datetime(2014, 1, 1, tzinfo=UTC)
    return [
        FakeAsset(
            item=CatalogItem(
                asset_id=f"a{i:03d}",
                created_at=base + timedelta(days=i),
                size_bytes=size,
                albums=[f"Album {i % 3}"],
                original_filename=f"a{i:03d}.HEIC",
                has_live_photo=with_live,
                has_edits=False,
                mime_type="image/heic",
                icloud_checksum=None,
            ),
            original_bytes=b"X" * size,
            live_photo_bytes=(b"L" * size) if with_live else None,
        )
        for i in range(count)
    ]


def _swap_to_jpeg(asset: FakeAsset, src_path: Path) -> None:
    """Replace asset's original_bytes with a real JPEG and update CatalogItem to match."""
    make_jpeg(src_path)
    asset.original_bytes = src_path.read_bytes()
    asset.item = CatalogItem(
        asset_id=asset.item.asset_id,
        created_at=asset.item.created_at,
        size_bytes=len(asset.original_bytes),
        albums=asset.item.albums,
        original_filename=asset.item.original_filename.replace(".HEIC", ".jpg"),
        has_live_photo=False,
        has_edits=False,
        mime_type="image/jpeg",
        icloud_checksum=None,
    )


def test_happy_path_all_items_deleted(tmp_path: Path) -> None:
    assets = _assets(3)
    for a in assets:
        _swap_to_jpeg(a, tmp_path / f"src_{a.item.asset_id}.jpg")

    fake = FakeICloudPhotos(assets=assets)
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    journal = Journal.open(tmp_path / "state.db")
    outcome = run_archival(
        client=fake,
        journal=journal,
        archive_root=archive_root,
        target_bytes=10_000,
        dry_run=False,
    )
    assert outcome.archived == 3
    assert outcome.deleted == 3
    assert outcome.failed_verify == 0
    assert list(fake.iter_oldest_first()) == []
    for a in assets:
        primary = archive_root / a.item.albums[0] / a.item.original_filename
        assert primary.is_file()


def test_verify_failure_preserves_icloud_copy(tmp_path: Path) -> None:
    assets = _assets(2)
    for a in assets:
        _swap_to_jpeg(a, tmp_path / f"src_{a.item.asset_id}.jpg")

    fake = FakeICloudPhotos(assets=assets)
    fake.truncate_download_for[assets[0].item.asset_id] = 50  # break first asset

    journal = Journal.open(tmp_path / "state.db")
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    outcome = run_archival(
        client=fake,
        journal=journal,
        archive_root=archive_root,
        target_bytes=10_000,
        dry_run=False,
    )
    assert outcome.failed_verify == 1
    assert outcome.deleted == 1
    remaining = [i.asset_id for i in fake.iter_oldest_first()]
    assert assets[0].item.asset_id in remaining


def test_dry_run_writes_no_disk_or_icloud(tmp_path: Path) -> None:
    assets = _assets(3)
    fake = FakeICloudPhotos(assets=assets)
    journal = Journal.open(tmp_path / "state.db")
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    outcome = run_archival(
        client=fake,
        journal=journal,
        archive_root=archive_root,
        target_bytes=10_000,
        dry_run=True,
    )
    assert outcome.archived == 0
    assert outcome.deleted == 0
    assert len(list(fake.iter_oldest_first())) == 3
    # Archive root may contain only .scratch (then cleaned up) — check no real files.
    real_dirs = [p for p in archive_root.iterdir() if not p.name.startswith(".")]
    assert real_dirs == []
    assert len(outcome.plan_rows) == 3


def test_run_aborts_when_free_space_insufficient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If projected_download x 1.2 > free space, abort before any download."""
    assets = _assets(3, size=10_000_000)
    fake = FakeICloudPhotos(assets=assets)
    journal = Journal.open(tmp_path / "state.db")
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    monkeypatch.setattr("icloud_archiver.orchestrator._free_bytes", lambda _p: 1_000_000)

    with pytest.raises(InsufficientSpace):
        run_archival(
            client=fake,
            journal=journal,
            archive_root=archive_root,
            target_bytes=100_000_000,
            dry_run=False,
        )
    assert len(list(fake.iter_oldest_first())) == 3
    # Archive may contain an empty .scratch dir — no real files
    real = [p for p in archive_root.iterdir() if not p.name.startswith(".")]
    assert real == []


def test_resume_reprocesses_non_terminal_items(tmp_path: Path) -> None:
    """An item left PLANNED by a crashed prior run gets re-processed, not skipped."""
    assets = _assets(1)
    _swap_to_jpeg(assets[0], tmp_path / "src.jpg")

    fake = FakeICloudPhotos(assets=assets)
    journal = Journal.open(tmp_path / "state.db")
    archive_root = tmp_path / "archive"
    archive_root.mkdir()

    prior_run = journal.start_run(target_bytes=1, dry_run=False, archive_root=str(archive_root))
    journal.upsert_item(assets[0].item, prior_run, ItemState.PLANNED)
    journal.end_run(prior_run, RunStatus.CRASHED)

    outcome = run_archival(
        client=fake,
        journal=journal,
        archive_root=archive_root,
        target_bytes=10_000,
        dry_run=False,
    )
    assert outcome.archived == 1
    assert outcome.deleted == 1
    assert list(fake.iter_oldest_first()) == []


def test_resume_skips_already_deleted_items(tmp_path: Path) -> None:
    assets = _assets(2)
    for a in assets:
        _swap_to_jpeg(a, tmp_path / f"src_{a.item.asset_id}.jpg")

    fake = FakeICloudPhotos(assets=assets)
    journal = Journal.open(tmp_path / "state.db")
    archive_root = tmp_path / "archive"
    archive_root.mkdir()

    # Run 1: archive both
    run_archival(
        client=fake, journal=journal, archive_root=archive_root,
        target_bytes=10_000, dry_run=False,
    )
    # Run 2: nothing left to do
    outcome2 = run_archival(
        client=fake, journal=journal, archive_root=archive_root,
        target_bytes=10_000, dry_run=False,
    )
    assert outcome2.archived == 0
    assert outcome2.deleted == 0


def test_resume_archived_item_skips_to_delete(tmp_path: Path) -> None:
    """An item already ARCHIVED from a prior run should skip download/verify/organize."""
    assets = _assets(1)
    _swap_to_jpeg(assets[0], tmp_path / "src.jpg")
    item = assets[0].item

    fake = FakeICloudPhotos(assets=assets)
    journal = Journal.open(tmp_path / "state.db")
    archive_root = tmp_path / "archive"
    archive_root.mkdir()

    # Simulate prior crashed run that reached ARCHIVED.
    prior_run = journal.start_run(target_bytes=1, dry_run=False, archive_root=str(archive_root))
    journal.upsert_item(item, prior_run, ItemState.PLANNED)
    journal.transition(item.asset_id, ItemState.DOWNLOADING, run_id=prior_run)
    journal.transition(item.asset_id, ItemState.DOWNLOADED, run_id=prior_run)
    journal.transition(item.asset_id, ItemState.VERIFIED, run_id=prior_run, sha256="x" * 64)
    journal.transition(
        item.asset_id, ItemState.ARCHIVED, run_id=prior_run, primary_path="/fake/path"
    )
    journal.end_run(prior_run, RunStatus.CRASHED)

    # Track whether fetch_item is called — it shouldn't be on the shortcut path.
    fetched: list[str] = []
    original_fetch = orch.fetch_item
    def spy_fetch(item, client, *, scratch_dir):
        fetched.append(item.asset_id)
        return original_fetch(item, client, scratch_dir=scratch_dir)
    orch.fetch_item = spy_fetch
    try:
        outcome = run_archival(
            client=fake, journal=journal, archive_root=archive_root,
            target_bytes=10_000, dry_run=False,
        )
    finally:
        orch.fetch_item = original_fetch

    # No download should have happened
    assert fetched == []
    # Delete should have happened
    assert outcome.deleted == 1
    assert list(fake.iter_oldest_first()) == []
    # bytes_archived stays 0 in this run because the archive work was done previously
    assert outcome.bytes_archived == 0
