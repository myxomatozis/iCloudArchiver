"""Wrap pyicloud's PhotosService into our ICloudPhotos protocol."""

import mimetypes
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from tqdm import tqdm

from icloud_archiver.types import CatalogItem

try:
    from pyicloud.services.photos import (  # type: ignore[import-untyped]
        SmartPhotoAlbum as _SmartPhotoAlbum,
    )
except ImportError:  # pragma: no cover — only missing in unit-test environments
    _SmartPhotoAlbum = None


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


def _photo_to_catalog_item(photo: Any, *, albums: list[str] | None = None) -> CatalogItem:
    if albums is None:
        albums = _normalize_albums(list(getattr(photo, "albums", [])))
    # pyicloud 2.x removed the "edited" / "alternative" version keys;
    # edited versions are no longer exposed via the CloudKit API.
    has_edits = False
    return CatalogItem(
        asset_id=str(photo.id),
        created_at=photo.created,
        size_bytes=int(photo.size),
        albums=albums,
        original_filename=str(photo.filename),
        has_live_photo=bool(getattr(photo, "is_live_photo", False)),
        has_edits=has_edits,
        mime_type=_guess_mime(str(photo.filename)),
        icloud_checksum=None,  # Apple doesn't expose a stable per-asset checksum
    )


class RealICloudPhotos:
    """ICloudPhotos protocol implementation backed by pyicloud."""

    def __init__(self, pyicloud_service: Any) -> None:
        self._svc = pyicloud_service
        self._album_index: dict[str, list[str]] | None = None
        self._photo_cache: dict[str, Any] = {}  # asset_id → live PhotoAsset

    # ------------------------------------------------------------------
    # Album index
    # ------------------------------------------------------------------

    def _build_album_index(self) -> dict[str, list[str]]:
        """Build {asset_id → [album_fullname, ...]} for all user-created albums.

        In pyicloud 2.x, PhotoAsset has no .albums back-reference; the
        relationship is album → photos only.  We invert it here by walking
        every non-smart album once and caching the result.
        """
        if self._album_index is not None:
            return self._album_index

        all_albums = list(self._svc.photos.albums)  # fetch metadata only
        user_albums = [
            a for a in all_albums if _SmartPhotoAlbum is None or not isinstance(a, _SmartPhotoAlbum)
        ]

        index: dict[str, list[str]] = {}
        with tqdm(
            user_albums,
            desc="Indexing albums",
            unit=" album",
            file=sys.stderr,
            dynamic_ncols=True,
        ) as bar:
            for album in bar:
                bar.set_postfix_str(album.fullname[:35])
                for photo in album:
                    aid = str(photo.id)
                    name = album.fullname
                    if aid not in index:
                        index[aid] = [name]
                    elif name not in index[aid]:
                        index[aid].append(name)

        self._album_index = index
        return index

    # ------------------------------------------------------------------
    # ICloudPhotos protocol
    # ------------------------------------------------------------------

    def iter_oldest_first(self) -> Iterator[CatalogItem]:
        """Yield CatalogItems sorted oldest-first by iCloud asset date.

        Two progress bars are shown on stderr:
          1. "Indexing albums"  — builds the album reverse-index (once, cached).
          2. "Scanning library" — pages through All Photos oldest-first.

        pyicloud's All Photos album is physically ordered newest-first (matching
        the Photos.app timeline).  Its default direction is DESCENDING, which
        pyicloud iterates by starting at offset ``len-1`` (the tail of the list
        = the oldest photo) and paging backward toward newer items.  Setting
        direction to ASCENDING would instead start at offset 0 (the newest
        photo) and page forward — i.e. newest-first, which is the wrong order.
        So we deliberately leave ``_direction`` at its default DESCENDING.

        Note on dates: iCloud sorts by ``assetDate``, which Photos.app also
        uses for the timeline.  For photos imported without valid EXIF dates
        (e.g. scans, old-camera files), Photos.app sets ``assetDate`` to the
        import date — not the original capture date.  To fix this, edit the
        date on those photos in Photos.app (⌘+I → Date field).
        """
        album_index = self._build_album_index()

        all_album = self._svc.photos.all
        # Do NOT override _direction here.  The default DESCENDING makes
        # pyicloud start at startRank=len-1 (oldest photo) and page toward
        # newer items, giving the oldest-first ordering we need.

        try:
            for photo in all_album:
                self._photo_cache[str(photo.id)] = photo  # keep alive for _find
                albums = _normalize_albums(album_index.get(str(photo.id), []))
                yield _photo_to_catalog_item(photo, albums=albums)
        except GeneratorExit:
            pass

    def download_original(self, asset_id: str, dest: Path) -> None:
        self._download(asset_id, "original", dest)

    def download_live_photo(self, asset_id: str, dest: Path) -> None:
        self._download(asset_id, "original_video", dest)

    def download_edited(self, asset_id: str, dest: Path) -> None:
        self._download(asset_id, "edited", dest)

    def _download(self, asset_id: str, variant: str, dest: Path) -> None:
        photo = self._find(asset_id)
        # pyicloud 2.x: download() returns Optional[bytes] directly (no streaming context manager).
        data = photo.download(variant)
        if data is None:
            raise ValueError(f"variant '{variant}' not available for asset {asset_id}")
        dest.write_bytes(data)

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
        # Fast path: photo was seen during the catalog scan.
        cached = self._photo_cache.get(asset_id)
        if cached is not None:
            return cached
        # No scan happened (e.g. --from-plan), so the cache is cold. Fetch this
        # one photo by id with a single targeted CloudKit query instead of
        # paging the whole library: pyicloud filters on recordName == asset_id,
        # and our asset_id IS that recordName (PhotoAsset.id). This keeps lookups
        # O(1) per item — the old full-library walk was O(N) per item / O(N^2)
        # per run, which made --from-plan downloads climb to ~60s/item.
        photo = self._svc.photos.all.get(asset_id)
        if photo is None:
            raise KeyError(f"photo not found in library: {asset_id}")
        self._photo_cache[asset_id] = photo
        return photo
