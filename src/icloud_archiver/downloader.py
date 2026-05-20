"""Per-item fetch into a scratch directory. Uses the ICloudPhotos protocol."""

import contextlib
import os
from pathlib import Path

from icloud_archiver.icloud_iface import ICloudPhotos
from icloud_archiver.organizer import DownloadedFiles
from icloud_archiver.types import CatalogItem


class DownloadError(Exception):
    pass


def _suffix(filename: str) -> str:
    return Path(filename).suffix or ".bin"


def _fsync_and_rename(partial: Path, final: Path) -> None:
    """Flush *partial* to disk before atomically renaming it to *final*.

    Without the fsync a power loss after the rename can leave *final* empty or
    truncated, since the rename can reach disk before the file's data does.
    """
    fd = os.open(str(partial), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    partial.rename(final)


def fetch_item(item: CatalogItem, client: ICloudPhotos, *, scratch_dir: Path) -> DownloadedFiles:
    """Download original + (optional) live photo + (optional) edited to scratch_dir.

    On any error, partial files are removed before re-raising as DownloadError.
    """
    scratch_dir.mkdir(parents=True, exist_ok=True)
    original = scratch_dir / f"{item.asset_id}_orig{_suffix(item.original_filename)}"
    partial = original.with_suffix(original.suffix + ".partial")
    live: Path | None = None
    edited: Path | None = None
    written: list[Path] = []
    try:
        client.download_original(item.asset_id, partial)
        _fsync_and_rename(partial, original)
        written.append(original)

        if item.has_live_photo:
            live = scratch_dir / f"{item.asset_id}_live.MOV"
            live_partial = live.with_suffix(live.suffix + ".partial")
            client.download_live_photo(item.asset_id, live_partial)
            _fsync_and_rename(live_partial, live)
            written.append(live)

        if item.has_edits:
            edited = scratch_dir / f"{item.asset_id}_edit{_suffix(item.original_filename)}"
            edit_partial = edited.with_suffix(edited.suffix + ".partial")
            client.download_edited(item.asset_id, edit_partial)
            _fsync_and_rename(edit_partial, edited)
            written.append(edited)

        return DownloadedFiles(original=original, live_photo=live, edited=edited)
    except Exception as exc:
        # Clean up everything we wrote so the next run starts clean
        for p in [*written, partial]:
            with contextlib.suppress(OSError):
                if p.exists():
                    p.unlink()
        for stray in scratch_dir.glob(f"{item.asset_id}_*.partial"):
            with contextlib.suppress(OSError):
                stray.unlink()
        raise DownloadError(f"failed to fetch {item.asset_id}: {exc}") from exc
