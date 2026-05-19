"""Place verified files into album folders, with hardlinks for multi-album items."""

import json
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from icloud_archiver.types import CatalogItem


@dataclass(frozen=True)
class DownloadedFiles:
    """Result of the downloader: paths inside the scratch directory."""

    original: Path
    live_photo: Path | None = None
    edited: Path | None = None


@dataclass(frozen=True)
class OrganizedPaths:
    primary: Path
    hardlinks: list[Path]
    sidecar_primary: Path
    sidecar_hardlinks: list[Path]


def sidecar_dict(item: CatalogItem, *, sha256: str, run_id: str) -> dict[str, Any]:
    return {
        "asset_id": item.asset_id,
        "original_filename": item.original_filename,
        "created_at": item.created_at.isoformat(timespec="seconds"),
        "mime_type": item.mime_type,
        "size_bytes": item.size_bytes,
        "sha256": sha256,
        "icloud_checksum": item.icloud_checksum,
        "albums": list(item.albums),
        "has_live_photo": item.has_live_photo,
        "has_edits": item.has_edits,
        "archived_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "archived_by_run": run_id,
    }


def _primary_folder(item: CatalogItem, archive_root: Path) -> Path:
    if item.albums:
        return archive_root / item.albums[0]
    return (
        archive_root
        / "_NoAlbum"
        / f"{item.created_at.year:04d}"
        / f"{item.created_at.month:02d}"
    )


def _additional_folders(item: CatalogItem, archive_root: Path) -> list[Path]:
    return [archive_root / a for a in item.albums[1:]]


def _move_or_skip(src: Path, dest: Path) -> None:
    """Move src → dest. If dest already exists, drop src (idempotent re-run)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        if src.exists():
            src.unlink()
        return
    shutil.move(str(src), str(dest))


def _hardlink_or_skip(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        # Already in place (idempotent); if it's a different inode, leave the
        # existing file alone — divergence will surface in caller's journal.
        return
    os.link(src, dest)


def _live_photo_name(original_filename: str) -> str:
    return f"{Path(original_filename).stem}_LIVE.MOV"


def _edited_name(original_filename: str) -> str:
    p = Path(original_filename)
    return f"{p.stem}_EDITED{p.suffix}"


def organize(
    item: CatalogItem,
    files: DownloadedFiles,
    archive_root: Path,
    *,
    sidecar: dict[str, Any],
) -> OrganizedPaths:
    primary_dir = _primary_folder(item, archive_root)
    primary_dir.mkdir(parents=True, exist_ok=True)

    primary = primary_dir / item.original_filename
    _move_or_skip(files.original, primary)

    live_primary: Path | None = None
    if files.live_photo is not None:
        live_primary = primary_dir / _live_photo_name(item.original_filename)
        _move_or_skip(files.live_photo, live_primary)

    edited_primary: Path | None = None
    if files.edited is not None:
        edited_primary = primary_dir / _edited_name(item.original_filename)
        _move_or_skip(files.edited, edited_primary)

    sidecar_primary = primary_dir / (Path(item.original_filename).stem + ".json")
    sidecar_primary.write_text(json.dumps(sidecar, indent=2, sort_keys=True))

    hardlinks: list[Path] = []
    sidecar_hardlinks: list[Path] = []
    for extra_dir in _additional_folders(item, archive_root):
        extra_dir.mkdir(parents=True, exist_ok=True)
        extra_primary = extra_dir / item.original_filename
        _hardlink_or_skip(primary, extra_primary)
        hardlinks.append(extra_primary)
        if live_primary is not None:
            _hardlink_or_skip(live_primary, extra_dir / live_primary.name)
        if edited_primary is not None:
            _hardlink_or_skip(edited_primary, extra_dir / edited_primary.name)
        extra_sidecar = extra_dir / sidecar_primary.name
        _hardlink_or_skip(sidecar_primary, extra_sidecar)
        sidecar_hardlinks.append(extra_sidecar)

    # fsync primary directory so renames + sidecar are durable
    fd = os.open(str(primary_dir), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

    return OrganizedPaths(
        primary=primary,
        hardlinks=hardlinks,
        sidecar_primary=sidecar_primary,
        sidecar_hardlinks=sidecar_hardlinks,
    )
