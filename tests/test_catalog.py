from collections.abc import Iterator
from datetime import UTC, datetime

from icloud_archiver.catalog import RealICloudPhotos, _normalize_albums, _photo_to_catalog_item


def test_normalize_albums_sorts_case_insensitive() -> None:
    assert _normalize_albums(["zebra", "Apple", "bear"]) == ["Apple", "bear", "zebra"]


def test_normalize_albums_handles_folder_nesting() -> None:
    # Tuples are how pyicloud-ipd exposes folder-album paths in some versions
    result = _normalize_albums([("Family", "Italy 2014"), "Highlights"])
    assert result == ["Family/Italy 2014", "Highlights"]


def test_photo_to_catalog_item_maps_required_fields() -> None:
    class _FakePhoto:
        id = "ABCDEF"
        filename = "IMG_0001.HEIC"
        size = 12345
        created = datetime(2014, 8, 23, 15, 42, 1, tzinfo=UTC)
        item_type = "image"
        is_live_photo = True  # pyicloud 2.x bool flag

        @property
        def albums(self) -> list[str]:
            return ["B Album", "A Album"]

    item = _photo_to_catalog_item(_FakePhoto())
    assert item.asset_id == "ABCDEF"
    assert item.original_filename == "IMG_0001.HEIC"
    assert item.size_bytes == 12345
    assert item.albums == ["A Album", "B Album"]
    assert item.has_live_photo is True
    assert item.has_edits is False
    assert item.mime_type == "image/heic"


class _FakePhotoAsset:
    def __init__(self, pid: str) -> None:
        self.id = pid


class _CountingAll:
    """Stands in for svc.photos.all; tracks by-id gets and full iterations."""

    def __init__(self, photos: list[_FakePhotoAsset]) -> None:
        self._by_id = {p.id: p for p in photos}
        self.iter_count = 0
        self.get_count = 0

    def __iter__(self) -> Iterator[_FakePhotoAsset]:
        self.iter_count += 1
        return iter(self._by_id.values())

    def get(self, key: str) -> _FakePhotoAsset | None:
        self.get_count += 1
        return self._by_id.get(key)


class _FakePhotosService:
    def __init__(self, all_: _CountingAll) -> None:
        self.all = all_


class _FakeSvc:
    def __init__(self, photos: _FakePhotosService) -> None:
        self.photos = photos


def test_find_fetches_by_id_without_paging_library() -> None:
    """Regression: --from-plan starts with a cold cache, so every item triggers
    _find. _find must resolve each asset with a targeted by-id fetch and never
    page the whole library (the O(N^2) walk that made downloads ~60s/item and
    forced a multi-minute upfront indexing pass).
    """
    photos = [_FakePhotoAsset(f"id{i}") for i in range(50)]
    counting = _CountingAll(photos)
    client = RealICloudPhotos(_FakeSvc(_FakePhotosService(counting)))

    # Look up every asset with a cold cache, mimicking the --from-plan run loop.
    for i in range(50):
        assert client._find(f"id{i}").id == f"id{i}"

    assert counting.iter_count == 0, "must not page the library; fetch by id instead"
    assert counting.get_count == 50, "each cold-cache lookup should be one by-id fetch"

    # A repeat lookup is served from cache — no extra network fetch.
    assert client._find("id0").id == "id0"
    assert counting.get_count == 50


def test_find_raises_keyerror_for_unknown_asset() -> None:
    counting = _CountingAll([_FakePhotoAsset("id0")])
    client = RealICloudPhotos(_FakeSvc(_FakePhotosService(counting)))
    try:
        client._find("missing")
    except KeyError:
        pass
    else:  # pragma: no cover - explicit failure path
        raise AssertionError("expected KeyError for unknown asset id")
