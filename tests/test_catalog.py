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



# --- records/lookup-by-id fakes ---

_ZONE = {
    "zoneName": "PrimarySync",
    "ownerRecordName": "_owner",
    "zoneType": "REGULAR_CUSTOM_ZONE",
}


class _LookupResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _LookupSession:
    """Serves records/lookup POSTs from a {recordName: record} table."""

    def __init__(self, records: dict[str, dict]) -> None:
        self._records = records
        self.posts: list[tuple[str, dict]] = []

    def post(self, url: str, json: dict, headers: dict) -> _LookupResponse:
        self.posts.append((url, json))
        recs = [
            self._records.get(
                r["recordName"],
                {"recordName": r["recordName"], "serverErrorCode": "NOT_FOUND"},
            )
            for r in json["records"]
        ]
        return _LookupResponse({"records": recs})


class _LookupLibrary:
    def __init__(self, zone: dict) -> None:
        self.zone_id = zone


class _LookupPhotosSvc:
    def __init__(self, session: _LookupSession) -> None:
        self.session = session
        self.service_endpoint = "https://ck.example/db"
        self.params = {"dsid": "X"}
        self.libraries = {"PrimarySync": _LookupLibrary(_ZONE)}


class _LookupSvc:
    def __init__(self, records: dict[str, dict]) -> None:
        self.session = _LookupSession(records)
        self.photos = _LookupPhotosSvc(self.session)


def _asset_record(asset_id: str = "ASSET1", master_id: str = "MASTER1") -> dict:
    return {
        "recordName": asset_id,
        "recordType": "CPLAsset",
        "recordChangeTag": "tag1",
        "fields": {"masterRef": {"value": {"recordName": master_id}}},
    }


def _master_record(master_id: str = "MASTER1") -> dict:
    return {"recordName": master_id, "recordType": "CPLMaster", "fields": {}}


def test_zone_id_fetched_once() -> None:
    svc = _LookupSvc({})
    client = RealICloudPhotos(svc)
    assert client._zone_id == _ZONE
    assert client._zone_id is client._zone_cache  # cached after first access


def test_lookup_photo_resolves_by_id_with_two_lookups() -> None:
    svc = _LookupSvc({"ASSET1": _asset_record(), "MASTER1": _master_record()})
    client = RealICloudPhotos(svc)

    photo = client._lookup_photo("ASSET1")

    assert photo.id == "ASSET1"
    # Two lookups, in order: the asset, then its master.
    assert [b["records"][0]["recordName"] for (_u, b) in svc.session.posts] == [
        "ASSET1",
        "MASTER1",
    ]
    # Every lookup carried the zone; zoneID injected into the asset record for delete().
    assert all(b["zoneID"] == _ZONE for (_u, b) in svc.session.posts)
    assert photo._asset_record["zoneID"] == _ZONE


def test_lookup_photo_raises_keyerror_when_asset_not_found() -> None:
    client = RealICloudPhotos(_LookupSvc({}))  # nothing resolves → NOT_FOUND
    try:
        client._lookup_photo("MISSING")
    except KeyError as exc:
        assert "MISSING" in str(exc)
    else:  # pragma: no cover - explicit failure path
        raise AssertionError("expected KeyError for missing asset")


def test_lookup_photo_raises_keyerror_when_master_missing() -> None:
    # Asset resolves but its master record does not.
    client = RealICloudPhotos(_LookupSvc({"ASSET1": _asset_record()}))
    try:
        client._lookup_photo("ASSET1")
    except KeyError as exc:
        assert "ASSET1" in str(exc)
    else:  # pragma: no cover - explicit failure path
        raise AssertionError("expected KeyError for missing master")


def test_find_looks_up_on_miss_then_caches() -> None:
    svc = _LookupSvc({"ASSET1": _asset_record(), "MASTER1": _master_record()})
    client = RealICloudPhotos(svc)

    first = client._find("ASSET1")
    assert first.id == "ASSET1"
    posts_after_first = len(svc.session.posts)

    # Second lookup of the same id is served from cache — no further POSTs.
    second = client._find("ASSET1")
    assert second is first
    assert len(svc.session.posts) == posts_after_first


def test_find_raises_keyerror_for_unknown_asset() -> None:
    client = RealICloudPhotos(_LookupSvc({}))
    try:
        client._find("missing")
    except KeyError as exc:
        assert "missing" in str(exc)
    else:  # pragma: no cover - explicit failure path
        raise AssertionError("expected KeyError for unknown asset id")


class _StreamSvc:
    """Minimal service exposing only .session, for _download's _stream_to_file."""

    def __init__(self, session: _FakeSession) -> None:
        self.session = session


def test_download_streams_to_dest(tmp_path: Path) -> None:
    photo = _FakeDownloadPhoto("id1", "https://example/asset")
    session = _FakeSession(_FakeResponse([b"chunk-A", b"chunk-B"]))
    client = RealICloudPhotos(_StreamSvc(session))
    client._photo_cache["id1"] = photo  # pre-seed: _find hits cache, no lookup

    dest = tmp_path / "id1_orig.jpg"
    client._download("id1", "original", dest)

    assert dest.read_bytes() == b"chunk-Achunk-B"
    assert session.stream_arg is True
    assert session.url == "https://example/asset"


def test_download_raises_when_variant_unavailable(tmp_path: Path) -> None:
    photo = _FakeDownloadPhoto("id1", None)  # download_url -> None
    session = _FakeSession(_FakeResponse([b"unused"]))
    client = RealICloudPhotos(_StreamSvc(session))
    client._photo_cache["id1"] = photo

    dest = tmp_path / "id1_orig.jpg"
    try:
        client._download("id1", "edited", dest)
    except ValueError as exc:
        assert "edited" in str(exc)
    else:  # pragma: no cover - explicit failure path
        raise AssertionError("expected ValueError for unavailable variant")

    assert not dest.exists()
