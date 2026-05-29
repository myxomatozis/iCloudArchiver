from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

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


class _FakeResponse:
    def __init__(self, chunks: list[bytes], status_ok: bool = True) -> None:
        self._chunks = chunks
        self._status_ok = status_ok
        self.raise_for_status_called = False

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def raise_for_status(self) -> None:
        self.raise_for_status_called = True
        if not self._status_ok:
            raise RuntimeError("HTTP 404")

    def iter_content(self, chunk_size: int) -> Iterator[bytes]:
        assert chunk_size > 0
        yield from self._chunks


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.stream_arg: bool | None = None
        self.url: str | None = None

    def get(self, url: str, stream: bool = False) -> _FakeResponse:
        self.url = url
        self.stream_arg = stream
        return self._response


def test_stream_to_file_writes_all_chunks(tmp_path: Path) -> None:
    from icloud_archiver.catalog import _stream_to_file

    chunks = [b"hello ", b"streamed ", b"world"]
    session = _FakeSession(_FakeResponse(chunks))
    dest = tmp_path / "out.bin"

    _stream_to_file(session, "https://example/asset", dest)

    assert dest.read_bytes() == b"".join(chunks)
    assert session.stream_arg is True
    assert session.url == "https://example/asset"


def test_stream_to_file_skips_empty_keepalive_chunks(tmp_path: Path) -> None:
    from icloud_archiver.catalog import _stream_to_file

    session = _FakeSession(_FakeResponse([b"a", b"", b"b", b""]))
    dest = tmp_path / "out.bin"

    _stream_to_file(session, "https://example/asset", dest)

    assert dest.read_bytes() == b"ab"


def test_stream_to_file_raises_on_http_error(tmp_path: Path) -> None:
    from icloud_archiver.catalog import _stream_to_file

    response = _FakeResponse([b"ignored"], status_ok=False)
    session = _FakeSession(response)
    dest = tmp_path / "out.bin"

    try:
        _stream_to_file(session, "https://example/asset", dest)
    except RuntimeError:
        pass
    else:  # pragma: no cover - explicit failure path
        raise AssertionError("expected RuntimeError from raise_for_status")

    assert response.raise_for_status_called is True
    assert not dest.exists(), "failed download must not leave a partial file"


class _FakeDownloadPhoto:
    def __init__(self, pid: str, url: str | None) -> None:
        self.id = pid
        self._url = url

    def download_url(self, variant: str) -> str | None:
        return self._url


class _FakeServiceWithSession:
    """Exposes .photos.all (by-id lookup) and .session (streaming)."""

    def __init__(self, photo: _FakeDownloadPhoto, session: _FakeSession) -> None:
        self.photos = _FakePhotosService(_CountingAll([photo]))  # type: ignore[arg-type]
        self.session = session


def test_download_streams_to_dest(tmp_path: Path) -> None:
    photo = _FakeDownloadPhoto("id1", "https://example/asset")
    session = _FakeSession(_FakeResponse([b"chunk-A", b"chunk-B"]))
    client = RealICloudPhotos(_FakeServiceWithSession(photo, session))
    dest = tmp_path / "id1_orig.jpg"

    client._download("id1", "original", dest)

    assert dest.read_bytes() == b"chunk-Achunk-B"
    assert session.stream_arg is True
    assert session.url == "https://example/asset"


def test_download_raises_when_variant_unavailable(tmp_path: Path) -> None:
    photo = _FakeDownloadPhoto("id1", None)  # download_url -> None
    session = _FakeSession(_FakeResponse([b"unused"]))
    client = RealICloudPhotos(_FakeServiceWithSession(photo, session))
    dest = tmp_path / "id1_orig.jpg"

    try:
        client._download("id1", "edited", dest)
    except ValueError as exc:
        assert "edited" in str(exc)
    else:  # pragma: no cover - explicit failure path
        raise AssertionError("expected ValueError for unavailable variant")

    assert not dest.exists()
