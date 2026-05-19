from datetime import UTC, datetime

from icloud_archiver.catalog import _normalize_albums, _photo_to_catalog_item


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
