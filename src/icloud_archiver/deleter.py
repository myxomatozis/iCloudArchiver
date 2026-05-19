"""Delete a single iCloud asset. Idempotent — deleting a missing asset is success."""

from icloud_archiver.icloud_iface import ICloudPhotos
from icloud_archiver.types import CatalogItem


class DeleteError(Exception):
    pass


def delete_asset(item: CatalogItem, client: ICloudPhotos) -> None:
    try:
        client.delete(item.asset_id)
    except KeyError:
        # already gone — treat as success (idempotency)
        return
    except Exception as exc:
        raise DeleteError(f"failed to delete {item.asset_id}: {exc}") from exc
