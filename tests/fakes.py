"""In-memory FakeICloudPhotos for unit + integration tests."""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from icloud_archiver.types import CatalogItem


@dataclass
class FakeAsset:
    item: CatalogItem
    original_bytes: bytes
    live_photo_bytes: bytes | None = None
    edited_bytes: bytes | None = None


class FakeICloudPhotos:
    """Implements the same surface our real ICloudPhotos client exposes.

    Use the `fail_download_for` / `truncate_download_for` / `fail_delete_for`
    sets/dicts to inject failures for testing.
    """

    def __init__(self, assets: list[FakeAsset]) -> None:
        self._assets: dict[str, FakeAsset] = {a.item.asset_id: a for a in assets}
        self.fail_download_for: set[str] = set()
        self.truncate_download_for: dict[str, int] = {}
        self.fail_delete_for: set[str] = set()

    def iter_oldest_first(self) -> Iterator[CatalogItem]:
        for a in sorted(self._assets.values(), key=lambda x: x.item.created_at):
            yield a.item

    def download_original(self, asset_id: str, dest: Path) -> None:
        if asset_id in self.fail_download_for:
            raise OSError(f"injected download failure for {asset_id}")
        asset = self._assets[asset_id]
        data = asset.original_bytes
        if asset_id in self.truncate_download_for:
            data = data[: self.truncate_download_for[asset_id]]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    def download_live_photo(self, asset_id: str, dest: Path) -> None:
        asset = self._assets[asset_id]
        if asset.live_photo_bytes is None:
            raise FileNotFoundError(f"no live photo for {asset_id}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(asset.live_photo_bytes)

    def download_edited(self, asset_id: str, dest: Path) -> None:
        asset = self._assets[asset_id]
        if asset.edited_bytes is None:
            raise FileNotFoundError(f"no edited version for {asset_id}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(asset.edited_bytes)

    def delete(self, asset_id: str) -> None:
        if asset_id in self.fail_delete_for:
            raise OSError(f"injected delete failure for {asset_id}")
        if asset_id not in self._assets:
            raise KeyError(asset_id)
        del self._assets[asset_id]

    def empty_trash(self, asset_ids: list[str]) -> None:
        # Items are already gone after delete(); empty_trash is a no-op.
        return None
