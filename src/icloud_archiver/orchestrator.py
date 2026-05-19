"""High-level run loop binding catalog → selector → downloader → verifier → organizer → deleter."""

import hashlib
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from icloud_archiver.deleter import DeleteError, delete_asset
from icloud_archiver.downloader import DownloadError, fetch_item
from icloud_archiver.icloud_iface import ICloudPhotos
from icloud_archiver.journal import Journal
from icloud_archiver.organizer import organize, sidecar_dict
from icloud_archiver.reporting import PlanRow
from icloud_archiver.selector import select_until
from icloud_archiver.types import CatalogItem, ItemState, RunStatus
from icloud_archiver.verifier import VerifyError, verify


@dataclass
class RunOutcome:
    archived: int = 0
    deleted: int = 0
    failed_download: int = 0
    failed_verify: int = 0
    failed_delete: int = 0
    skipped: int = 0
    bytes_archived: int = 0
    plan_rows: list[PlanRow] = field(default_factory=list)


class InsufficientSpace(Exception):
    pass


def run_archival(
    *,
    client: ICloudPhotos,
    journal: Journal,
    archive_root: Path,
    target_bytes: int,
    dry_run: bool,
) -> RunOutcome:
    run_id = journal.start_run(
        target_bytes=target_bytes,
        dry_run=dry_run,
        archive_root=str(archive_root),
    )
    outcome = RunOutcome()
    scratch_dir = archive_root / ".scratch"
    # Wipe any leftover partials from a crashed previous run before starting fresh.
    if scratch_dir.exists():
        shutil.rmtree(scratch_dir, ignore_errors=True)
    scratch_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Materialize selection up-front so state mutations during processing
        # do not affect what items we iterate over.
        catalog = client.iter_oldest_first()
        selected = list(select_until(catalog, target_bytes=target_bytes, journal=journal))

        if dry_run:
            outcome.plan_rows.extend(
                PlanRow(
                    asset_id=item.asset_id,
                    created_at=item.created_at,
                    size_bytes=item.size_bytes,
                    albums=list(item.albums),
                )
                for item in selected
            )
            journal.end_run(run_id, RunStatus.COMPLETED)
            return outcome

        # Free-space check (spec §5.1 phase B, §7.2): need >= 1.2x projected.
        projected = sum(i.size_bytes for i in selected)
        required = int(projected * 1.2)
        free = _free_bytes(archive_root)
        if free < required:
            raise InsufficientSpace(
                f"need {required} bytes on archive drive (1.2x {projected}); "
                f"only {free} available"
            )

        for item in selected:
            journal.upsert_item(item, run_id, ItemState.PLANNED)
            _archive_one(item, client, journal, archive_root, scratch_dir, run_id, outcome)

        journal.end_run(run_id, RunStatus.COMPLETED)
    except KeyboardInterrupt:
        journal.end_run(run_id, RunStatus.ABORTED)
        raise
    except Exception:
        journal.end_run(run_id, RunStatus.CRASHED)
        raise
    finally:
        # Best-effort scratch cleanup (per-item cleanup happens inside downloader on error)
        if scratch_dir.exists() and not any(scratch_dir.iterdir()):
            shutil.rmtree(scratch_dir, ignore_errors=True)
    return outcome


def _free_bytes(path: Path) -> int:
    s = os.statvfs(path)
    return s.f_bavail * s.f_frsize


def _hash_first_4kb(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        h.update(f.read(4096))
    return h.hexdigest()


def _archive_one(
    item: CatalogItem,
    client: ICloudPhotos,
    journal: Journal,
    archive_root: Path,
    scratch_dir: Path,
    run_id: str,
    outcome: RunOutcome,
) -> bool:
    # Resume shortcut (spec §6.3): if a prior run already wrote the file to disk
    # and reached ARCHIVED or DELETING, skip download/verify/organize and go
    # straight to delete. (Earlier-state resumes have to re-download because we
    # wipe scratch at run start.)
    prior = journal.get_state(item.asset_id)
    if prior in (ItemState.ARCHIVED, ItemState.DELETING):
        journal.transition(item.asset_id, ItemState.DELETING, run_id=run_id)
        try:
            delete_asset(item, client)
        except DeleteError as exc:
            journal.transition(item.asset_id, ItemState.DELETING, run_id=run_id, error=str(exc))
            outcome.failed_delete += 1
            return True
        journal.transition(item.asset_id, ItemState.DELETED, run_id=run_id)
        outcome.deleted += 1
        outcome.bytes_archived += item.size_bytes
        return True

    # Download
    journal.transition(item.asset_id, ItemState.DOWNLOADING, run_id=run_id)
    try:
        files = fetch_item(item, client, scratch_dir=scratch_dir)
    except DownloadError as exc:
        journal.transition(
            item.asset_id, ItemState.FAILED_DOWNLOAD, run_id=run_id, error=str(exc)
        )
        outcome.failed_download += 1
        return False
    journal.transition(item.asset_id, ItemState.DOWNLOADED, run_id=run_id)

    # Verify
    journal.transition(item.asset_id, ItemState.VERIFYING, run_id=run_id)
    try:
        result = verify(item, files.original)
    except VerifyError as exc:
        journal.transition(
            item.asset_id, ItemState.FAILED_VERIFY, run_id=run_id, error=str(exc)
        )
        outcome.failed_verify += 1
        for p in (files.original, files.live_photo, files.edited):
            if p is not None and p.exists():
                p.unlink()
        return False

    pre_organize_4kb = _hash_first_4kb(files.original)
    journal.transition(
        item.asset_id, ItemState.VERIFIED, run_id=run_id, sha256=result.sha256
    )

    # Organize
    journal.transition(item.asset_id, ItemState.ORGANIZING, run_id=run_id)
    side = sidecar_dict(item, sha256=result.sha256, run_id=run_id)
    organized = organize(item, files, archive_root, sidecar=side)

    # Post-organize readback (spec §7.1): first-4KB hash must match.
    post_organize_4kb = _hash_first_4kb(organized.primary)
    if post_organize_4kb != pre_organize_4kb:
        journal.transition(
            item.asset_id,
            ItemState.FAILED_VERIFY,
            run_id=run_id,
            error=f"post-organize 4KB hash mismatch: {pre_organize_4kb} vs {post_organize_4kb}",
        )
        outcome.failed_verify += 1
        return False

    journal.transition(
        item.asset_id,
        ItemState.ARCHIVED,
        run_id=run_id,
        primary_path=str(organized.primary),
        hardlink_paths=[str(p) for p in organized.hardlinks],
    )
    outcome.archived += 1
    outcome.bytes_archived += item.size_bytes

    # Delete
    journal.transition(item.asset_id, ItemState.DELETING, run_id=run_id)
    try:
        delete_asset(item, client)
    except DeleteError as exc:
        journal.transition(item.asset_id, ItemState.DELETING, run_id=run_id, error=str(exc))
        outcome.failed_delete += 1
        return True
    journal.transition(item.asset_id, ItemState.DELETED, run_id=run_id)
    outcome.deleted += 1
    return True
