"""Selection logic — yield items oldest-first until cumulative size ≥ target."""

from collections.abc import Iterable, Iterator

from icloud_archiver.journal import Journal
from icloud_archiver.types import CatalogItem


def select_until(
    items: Iterable[CatalogItem],
    *,
    target_bytes: int,
    journal: Journal,
) -> Iterator[CatalogItem]:
    """Yield items in input order until cumulative `size_bytes` ≥ target_bytes.

    Items in TERMINAL journal states (DELETED, SKIPPED, FAILED_VERIFY) are
    skipped — already done. Items in non-terminal states (e.g. PLANNED,
    DOWNLOADING from a crashed prior run) ARE re-yielded so the pipeline
    can re-process them. The pipeline operations (organize, delete) are
    idempotent, so re-processing is safe.
    """
    if target_bytes <= 0:
        return
    cumulative = 0
    for item in items:
        if cumulative >= target_bytes:
            return
        if journal.is_terminal(item.asset_id):
            continue
        yield item
        cumulative += item.size_bytes
