"""Place verified files into album folders, with hardlinks for multi-album items."""

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from icloud_archiver.types import CatalogItem
from icloud_archiver.verifier import sha256_of


class OrganizeError(Exception):
    """Raised when files cannot be placed safely (e.g. an unresolved collision)."""


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
        archive_root / "_NoAlbum" / f"{item.created_at.year:04d}" / f"{item.created_at.month:02d}"
    )


def _additional_folders(item: CatalogItem, archive_root: Path) -> list[Path]:
    return [archive_root / a for a in item.albums[1:]]


def _disambiguator(asset_id: str) -> str:
    """A short, filesystem-safe token unique to an asset, for qualifying names."""
    return hashlib.sha1(asset_id.encode("utf-8")).hexdigest()[:10]


def _resolve_name(item: CatalogItem, target_dirs: list[Path], *, src_sha: str) -> str:
    """Pick the on-disk filename for this asset.

    Uses ``original_filename`` unless it already exists — with *different*
    content — in any target folder. iCloud filenames are not unique, so a
    distinct asset sharing the name would otherwise overwrite or be dropped;
    in that case the name is qualified with an asset-derived token.
    """
    candidate = item.original_filename
    for folder in target_dirs:
        existing = folder / candidate
        if existing.exists() and sha256_of(existing) != src_sha:
            p = Path(candidate)
            return f"{p.stem}__{_disambiguator(item.asset_id)}{p.suffix}"
    return candidate


def _place_file(src: Path, dest: Path, *, src_sha: str) -> None:
    """Move src → dest. If dest exists it must be byte-identical to src (an
    idempotent re-run); a content mismatch is an unresolved collision and
    raises rather than silently discarding either file."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        if sha256_of(dest) != src_sha:
            raise OrganizeError(
                f"{dest} already exists with different content; refusing to overwrite"
            )
        if src.exists():
            src.unlink()
        return
    shutil.move(str(src), str(dest))


def _hardlink(src: Path, dest: Path, *, src_sha: str) -> None:
    """Hardlink src → dest. If dest exists it must already hold src's content."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        if sha256_of(dest) != src_sha:
            raise OrganizeError(
                f"{dest} already exists with different content; refusing to hardlink over it"
            )
        return
    os.link(src, dest)


def organize(
    item: CatalogItem,
    files: DownloadedFiles,
    archive_root: Path,
    *,
    sidecar: dict[str, Any],
) -> OrganizedPaths:
    primary_dir = _primary_folder(item, archive_root)
    additional_dirs = _additional_folders(item, archive_root)

    src_sha = sha256_of(files.original)
    name = _resolve_name(item, [primary_dir, *additional_dirs], src_sha=src_sha)
    stem, suffix = Path(name).stem, Path(name).suffix

    primary_dir.mkdir(parents=True, exist_ok=True)
    primary = primary_dir / name
    _place_file(files.original, primary, src_sha=src_sha)

    live_primary: Path | None = None
    live_sha = ""
    if files.live_photo is not None:
        live_sha = sha256_of(files.live_photo)
        live_primary = primary_dir / f"{stem}_LIVE.MOV"
        _place_file(files.live_photo, live_primary, src_sha=live_sha)

    edited_primary: Path | None = None
    edited_sha = ""
    if files.edited is not None:
        edited_sha = sha256_of(files.edited)
        edited_primary = primary_dir / f"{stem}_EDITED{suffix}"
        _place_file(files.edited, edited_primary, src_sha=edited_sha)

    sidecar_primary = primary_dir / f"{stem}.json"
    sidecar_primary.write_text(json.dumps(sidecar, indent=2, sort_keys=True))
    sidecar_sha = sha256_of(sidecar_primary)

    hardlinks: list[Path] = []
    sidecar_hardlinks: list[Path] = []
    for extra_dir in additional_dirs:
        extra_dir.mkdir(parents=True, exist_ok=True)
        extra_primary = extra_dir / name
        _hardlink(primary, extra_primary, src_sha=src_sha)
        hardlinks.append(extra_primary)
        if live_primary is not None:
            _hardlink(live_primary, extra_dir / live_primary.name, src_sha=live_sha)
        if edited_primary is not None:
            _hardlink(edited_primary, extra_dir / edited_primary.name, src_sha=edited_sha)
        extra_sidecar = extra_dir / sidecar_primary.name
        _hardlink(sidecar_primary, extra_sidecar, src_sha=sidecar_sha)
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
