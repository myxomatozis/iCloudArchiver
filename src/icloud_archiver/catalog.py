"""Wrap pyicloud-ipd's PhotosService into our ICloudPhotos protocol."""

import mimetypes
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from icloud_archiver.types import CatalogItem


def _normalize_albums(raw: list[Any]) -> list[str]:
    flat: list[str] = []
    for a in raw:
        if isinstance(a, str):
            flat.append(a)
        elif isinstance(a, tuple | list):
            flat.append("/".join(str(part) for part in a))
        else:
            flat.append(str(a))
    return sorted(flat, key=lambda s: s.lower())


_EXT_TO_MIME = {
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".mov": "video/quicktime",
    ".mp4": "video/mp4",
    ".gif": "image/gif",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


def _guess_mime(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in _EXT_TO_MIME:
        return _EXT_TO_MIME[ext]
    guess = mimetypes.guess_type(filename)[0]
    return guess or "application/octet-stream"


def _photo_to_catalog_item(photo: Any) -> CatalogItem:
    albums = _normalize_albums(list(getattr(photo, "albums", [])))
    versions = getattr(photo, "versions", {}) or {}
    has_edits = bool(versions.get("edited") or versions.get("alternative"))
    return CatalogItem(
        asset_id=str(photo.id),
        created_at=photo.created,
        size_bytes=int(photo.size),
        albums=albums,
        original_filename=str(photo.filename),
        has_live_photo=bool(getattr(photo, "live_photo_size", 0)),
        has_edits=has_edits,
        mime_type=_guess_mime(str(photo.filename)),
        icloud_checksum=None,  # Apple doesn't expose a stable per-asset checksum
    )


class RealICloudPhotos:
    """ICloudPhotos protocol implementation backed by pyicloud-ipd."""

    def __init__(self, pyicloud_service: Any) -> None:
        self._svc = pyicloud_service

    def iter_oldest_first(self) -> Iterator[CatalogItem]:
        all_album = self._svc.photos.albums["All Photos"]
        all_album.sort_direction = "ASCENDING"
        for photo in all_album:
            yield _photo_to_catalog_item(photo)

    def download_original(self, asset_id: str, dest: Path) -> None:
        self._download(asset_id, "original", dest)

    def download_live_photo(self, asset_id: str, dest: Path) -> None:
        self._download(asset_id, "originalVideo", dest)

    def download_edited(self, asset_id: str, dest: Path) -> None:
        self._download(asset_id, "edited", dest)

    def _download(self, asset_id: str, variant: str, dest: Path) -> None:
        photo = self._find(asset_id)
        with photo.download(variant) as resp, dest.open("wb") as out:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                out.write(chunk)

    def delete(self, asset_id: str) -> None:
        self._find(asset_id).delete()

    def empty_trash(self, asset_ids: list[str]) -> None:
        recently_deleted = self._svc.photos.albums.get("Recently Deleted")
        if recently_deleted is None:
            return
        target = set(asset_ids)
        for photo in recently_deleted:
            if str(photo.id) in target:
                photo.delete()  # permanent in Recently Deleted

    def _find(self, asset_id: str) -> Any:
        # Linear scan — iCloud's library API exposes no direct by-id lookup.
        for p in self._svc.photos.albums["All Photos"]:
            if str(p.id) == asset_id:
                return p
        raise KeyError(asset_id)
