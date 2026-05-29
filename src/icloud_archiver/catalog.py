"""Wrap pyicloud's PhotosService into our ICloudPhotos protocol."""

import mimetypes
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from pyicloud.const import CONTENT_TYPE, CONTENT_TYPE_TEXT  # type: ignore[import-untyped]
from pyicloud.services.photos import PhotoAsset  # type: ignore[import-untyped]
from tqdm import tqdm

from icloud_archiver.types import CatalogItem

try:
    from pyicloud.services.photos import (
        SmartPhotoAlbum as _SmartPhotoAlbum,
    )
except ImportError:  # pragma: no cover — only missing in unit-test environments
    _SmartPhotoAlbum = None

_DOWNLOAD_CHUNK_BYTES = 1024 * 1024  # 1 MiB; matches verifier's hash read size


def _stream_to_file(session: Any, url: str, dest: Path) -> None:
    """Stream an HTTP GET response body to *dest* in fixed-size chunks.

    Avoids holding the whole file in memory (pyicloud's PhotoAsset.download()
    does response.raw.read()). *session* is any requests.Session-like object.
    """
    with session.get(url, stream=True) as resp:
        resp.raise_for_status()
        with dest.open("wb") as f:
            for chunk in resp.iter_content(_DOWNLOAD_CHUNK_BYTES):
                if chunk:  # skip keep-alive empty chunks
                    f.write(chunk)


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
        self._zone_cache: dict[str, str] | None = None  # primary-library CloudKit zone (lazy)

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
        url = photo.download_url(variant)
        if url is None:
            raise ValueError(f"variant '{variant}' not available for asset {asset_id}")
        # Stream straight to disk; pyicloud's photo.download() would read the
        # whole file into memory (response.raw.read()).
        _stream_to_file(self._svc.session, url, dest)

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

    @property
    def _zone_id(self) -> dict[str, str]:
        """Primary-library CloudKit zone (incl. ownerRecordName), fetched once.

        Used both as the ``zoneID`` on records/lookup requests and injected into
        looked-up asset records so ``PhotoAsset.delete()`` (records/modify) works.
        """
        zone = self._zone_cache
        if zone is None:
            zone = self._svc.photos.libraries["PrimarySync"].zone_id
            self._zone_cache = zone
        return zone

    def _lookup_records(self, record_names: list[str]) -> dict[str, Any]:
        """POST records/lookup for *record_names*.

        Returns ``{recordName: record}`` for records that resolved; records that
        came back with a ``serverErrorCode`` (e.g. NOT_FOUND) are omitted.
        """
        url = (
            f"{self._svc.photos.service_endpoint}/records/lookup"
            f"?{urlencode(self._svc.photos.params)}"
        )
        body = {
            "records": [{"recordName": rn} for rn in record_names],
            "zoneID": self._zone_id,
        }
        resp = self._svc.photos.session.post(
            url, json=body, headers={CONTENT_TYPE: CONTENT_TYPE_TEXT}
        )
        return {
            rec["recordName"]: rec
            for rec in resp.json().get("records", [])
            if "serverErrorCode" not in rec
        }

    def _lookup_photo(self, asset_id: str) -> Any:
        """Resolve a live PhotoAsset by its CPLAsset recordName via two lookups.

        pyicloud's ``photos.all.get(id)`` cannot do this against the live API (the
        all-photos query rejects ``resultsLimit=1`` and ignores the recordName
        filter), so we hit the records/lookup endpoint directly: fetch the asset
        record, follow its ``masterRef`` to the master record (which holds the
        download URLs), and rebuild the ``PhotoAsset``.
        """
        asset_rec = self._lookup_records([asset_id]).get(asset_id)
        if asset_rec is None:
            raise KeyError(f"photo not found in library: {asset_id}")
        # The looked-up asset record omits zoneID; delete() reads it off the record.
        asset_rec.setdefault("zoneID", self._zone_id)
        master_id = asset_rec["fields"]["masterRef"]["value"]["recordName"]
        master_rec = self._lookup_records([master_id]).get(master_id)
        if master_rec is None:
            raise KeyError(f"master record missing for {asset_id}")
        return PhotoAsset(self._svc.photos, master_rec, asset_rec)

    def _find(self, asset_id: str) -> Any:
        # Fast path: photo already cached (the --target-freed scan populates the
        # cache as it iterates; see iter_oldest_first).
        cached = self._photo_cache.get(asset_id)
        if cached is not None:
            return cached
        # Cold cache (run --from-plan): resolve this id directly via
        # records/lookup. No library walk — missing items raise immediately.
        photo = self._lookup_photo(asset_id)
        self._photo_cache[asset_id] = photo
        return photo
