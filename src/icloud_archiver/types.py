"""Shared immutable data types."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ItemState(StrEnum):
    PLANNED = "PLANNED"
    DOWNLOADING = "DOWNLOADING"
    FAILED_DOWNLOAD = "FAILED_DOWNLOAD"
    DOWNLOADED = "DOWNLOADED"
    VERIFYING = "VERIFYING"
    FAILED_VERIFY = "FAILED_VERIFY"
    VERIFIED = "VERIFIED"
    ORGANIZING = "ORGANIZING"
    ARCHIVED = "ARCHIVED"
    DELETING = "DELETING"
    DELETED = "DELETED"
    SKIPPED = "SKIPPED"

    def is_terminal(self) -> bool:
        return self in _TERMINAL_STATES


_TERMINAL_STATES = frozenset({ItemState.DELETED, ItemState.SKIPPED, ItemState.FAILED_VERIFY})


class RunStatus(StrEnum):
    COMPLETED = "completed"
    ABORTED = "aborted"
    CRASHED = "crashed"


@dataclass(frozen=True)
class CatalogItem:
    """Immutable description of one iCloud asset to be archived.

    `albums` is sorted case-insensitively for determinism. albums[0] is the
    primary placement folder. Apple folder/album nesting flattens to
    "Parent/Child" strings.

    Callers MUST NOT mutate `albums` after construction. The dataclass is
    frozen against attribute reassignment but does not deep-freeze list
    contents; mutation would silently break the determinism invariant.
    """

    asset_id: str
    created_at: datetime
    size_bytes: int
    albums: list[str]
    original_filename: str
    has_live_photo: bool
    has_edits: bool
    mime_type: str
    icloud_checksum: str | None
