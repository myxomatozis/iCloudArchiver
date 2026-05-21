"""SQLite-backed journal — source of truth for archival state.

Single-writer guarantee required: the TOCTOU patterns in upsert_item/transition
(SELECT followed by INSERT/UPDATE in separate statements) are safe only when at
most one process holds a Journal connection at a time.
"""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ulid import ULID

from icloud_archiver.types import CatalogItem, ItemState, RunStatus

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id            TEXT PRIMARY KEY,
  started_at        TEXT NOT NULL,
  ended_at          TEXT,
  target_bytes      INTEGER NOT NULL,
  dry_run           INTEGER NOT NULL,
  archive_root      TEXT NOT NULL,
  ended_status      TEXT
);
CREATE TABLE IF NOT EXISTS items (
  asset_id          TEXT PRIMARY KEY,
  first_seen_run    TEXT NOT NULL,
  created_at        TEXT NOT NULL,
  size_bytes        INTEGER NOT NULL,
  state             TEXT NOT NULL,
  primary_path      TEXT,
  hardlink_paths    TEXT,
  sha256            TEXT,
  icloud_checksum   TEXT,
  error             TEXT,
  updated_at        TEXT NOT NULL,
  original_filename TEXT,
  albums            TEXT,
  has_live_photo    INTEGER,
  has_edits         INTEGER,
  mime_type         TEXT,
  FOREIGN KEY (first_seen_run) REFERENCES runs(run_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS item_events (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id            TEXT NOT NULL,
  asset_id          TEXT NOT NULL,
  at                TEXT NOT NULL,
  from_state        TEXT,
  to_state          TEXT NOT NULL,
  detail            TEXT,
  FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
  FOREIGN KEY (asset_id) REFERENCES items(asset_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS items_state_idx ON items(state);
CREATE INDEX IF NOT EXISTS items_created_idx ON items(created_at);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Journal:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        """Add any missing columns to the items table (safe for existing databases)."""
        existing = {r[1] for r in self._conn.execute("PRAGMA table_info(items)").fetchall()}
        new_columns = [
            ("original_filename", "TEXT"),
            ("albums", "TEXT"),
            ("has_live_photo", "INTEGER"),
            ("has_edits", "INTEGER"),
            ("mime_type", "TEXT"),
        ]
        for col_name, col_type in new_columns:
            if col_name not in existing:
                self._conn.execute(f"ALTER TABLE items ADD COLUMN {col_name} {col_type}")
        self._conn.commit()

    @classmethod
    def open(cls, path: Path) -> "Journal":
        path.parent.mkdir(parents=True, exist_ok=True)
        return cls(sqlite3.connect(str(path)))

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Journal":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- runs ---

    def start_run(self, *, target_bytes: int, dry_run: bool, archive_root: str) -> str:
        run_id = str(ULID())
        self._conn.execute(
            "INSERT INTO runs(run_id, started_at, target_bytes, dry_run, archive_root) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, _now(), target_bytes, int(dry_run), archive_root),
        )
        self._conn.commit()
        return run_id

    def end_run(self, run_id: str, status: RunStatus) -> None:
        cur = self._conn.execute(
            "UPDATE runs SET ended_at = ?, ended_status = ? WHERE run_id = ?",
            (_now(), status.value, run_id),
        )
        if cur.rowcount == 0:
            raise KeyError(f"run_id not in journal: {run_id}")
        self._conn.commit()

    def list_runs(self) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM runs ORDER BY started_at DESC").fetchall()
        return [dict(r) for r in rows]

    # --- items ---

    def upsert_item(self, item: CatalogItem, run_id: str, state: ItemState) -> None:
        existed = self._conn.execute(
            "SELECT 1 FROM items WHERE asset_id = ?", (item.asset_id,)
        ).fetchone()
        if existed:
            current = self.get_state(item.asset_id)
            if current is not None and current.is_terminal():
                return  # already done; do not re-open
            self.transition(item.asset_id, state, run_id=run_id)
            return
        ts = _now()
        cols = (
            "asset_id, first_seen_run, created_at, size_bytes, state, updated_at, "
            "original_filename, albums, has_live_photo, has_edits, mime_type"
        )
        self._conn.execute(
            f"INSERT INTO items({cols}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item.asset_id,
                run_id,
                item.created_at.isoformat(timespec="seconds"),
                item.size_bytes,
                state.value,
                ts,
                item.original_filename,
                json.dumps(item.albums),
                int(item.has_live_photo),
                int(item.has_edits),
                item.mime_type,
            ),
        )
        self._conn.execute(
            "INSERT INTO item_events(run_id, asset_id, at, from_state, to_state) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, item.asset_id, ts, None, state.value),
        )
        self._conn.commit()

    def transition(
        self,
        asset_id: str,
        new_state: ItemState,
        *,
        run_id: str,
        detail: dict[str, Any] | None = None,
        primary_path: str | None = None,
        hardlink_paths: list[str] | None = None,
        sha256: str | None = None,
        error: str | None = None,
    ) -> None:
        row = self._conn.execute(
            "SELECT state FROM items WHERE asset_id = ?", (asset_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"asset_id not in journal: {asset_id}")
        from_state = row["state"]
        ts = _now()  # captured ONCE for both updates

        sets = ["state = ?", "updated_at = ?"]
        params: list[Any] = [new_state.value, ts]
        if primary_path is not None:
            sets.append("primary_path = ?")
            params.append(primary_path)
        if hardlink_paths is not None:
            sets.append("hardlink_paths = ?")
            params.append(json.dumps(hardlink_paths))
        if sha256 is not None:
            sets.append("sha256 = ?")
            params.append(sha256)
        if error is not None:
            sets.append("error = ?")
            params.append(error)
        params.append(asset_id)
        self._conn.execute(f"UPDATE items SET {', '.join(sets)} WHERE asset_id = ?", params)

        self._conn.execute(
            "INSERT INTO item_events(run_id, asset_id, at, from_state, to_state, detail) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                run_id,
                asset_id,
                ts,  # same timestamp
                from_state,
                new_state.value,
                json.dumps(detail) if detail else None,
            ),
        )
        self._conn.commit()

    def get_state(self, asset_id: str) -> ItemState | None:
        row = self._conn.execute(
            "SELECT state FROM items WHERE asset_id = ?", (asset_id,)
        ).fetchone()
        return ItemState(row["state"]) if row else None

    def get_primary_path(self, asset_id: str) -> str | None:
        """Return the archived primary file path, or None if unset/unknown."""
        row = self._conn.execute(
            "SELECT primary_path FROM items WHERE asset_id = ?", (asset_id,)
        ).fetchone()
        return row["primary_path"] if row else None

    def is_terminal(self, asset_id: str) -> bool:
        """True iff the asset is in a terminal state (see ItemState._TERMINAL_STATES).

        Returns False for unknown asset_ids — selector should treat them as 'new'.
        Returns False for non-terminal known states (PLANNED/.../DELETING) so the
        resume pass can re-pick them up and run them through the idempotent pipeline.
        """
        state = self.get_state(asset_id)
        return state is not None and state.is_terminal()

    def events_for(self, asset_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM item_events WHERE asset_id = ? ORDER BY id ASC", (asset_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # --- resume ---

    def resumable_items(self) -> list[CatalogItem]:
        """Return items in non-terminal states. Used at run start to route resume work."""
        terminal = [s.value for s in ItemState if s.is_terminal()]
        q = (
            "SELECT asset_id, created_at, size_bytes, original_filename, albums, "
            "has_live_photo, has_edits, mime_type, icloud_checksum FROM items "
            f"WHERE state NOT IN ({','.join('?' * len(terminal))}) "
            "ORDER BY created_at ASC"
        )
        rows = self._conn.execute(q, terminal).fetchall()
        out: list[CatalogItem] = []
        for r in rows:
            out.append(
                CatalogItem(
                    asset_id=r["asset_id"],
                    created_at=datetime.fromisoformat(r["created_at"]),
                    size_bytes=r["size_bytes"],
                    albums=json.loads(r["albums"] or "[]"),
                    original_filename=r["original_filename"] or "",
                    has_live_photo=bool(r["has_live_photo"]),
                    has_edits=bool(r["has_edits"]),
                    mime_type=r["mime_type"] or "",
                    icloud_checksum=r["icloud_checksum"],
                )
            )
        return out

    def items_for_run(self, run_id: str) -> list[CatalogItem]:
        """Return CatalogItems planned in *run_id* that are still PLANNED.

        Used by ``run --from-plan`` to load the item list a preceding ``plan``
        run produced. We match via ``item_events`` (rows where this run wrote
        a PLANNED transition) rather than via ``items.first_seen_run``: that
        column is only set on INSERT and never updated, so an item re-planned
        by a later run still carries its original ``first_seen_run``, and a
        first_seen_run-based filter would miss it on the second plan.
        """
        rows = self._conn.execute(
            "SELECT i.asset_id, i.created_at, i.size_bytes, i.original_filename, i.albums, "
            "i.has_live_photo, i.has_edits, i.mime_type, i.icloud_checksum "
            "FROM items i "
            "WHERE i.state = ? AND EXISTS ("
            "  SELECT 1 FROM item_events e "
            "  WHERE e.asset_id = i.asset_id AND e.run_id = ? AND e.to_state = ?"
            ") "
            "ORDER BY i.created_at ASC",
            (ItemState.PLANNED.value, run_id, ItemState.PLANNED.value),
        ).fetchall()
        out: list[CatalogItem] = []
        for r in rows:
            out.append(
                CatalogItem(
                    asset_id=r["asset_id"],
                    created_at=datetime.fromisoformat(r["created_at"]),
                    size_bytes=r["size_bytes"],
                    albums=json.loads(r["albums"] or "[]"),
                    original_filename=r["original_filename"] or "",
                    has_live_photo=bool(r["has_live_photo"]),
                    has_edits=bool(r["has_edits"]),
                    mime_type=r["mime_type"] or "",
                    icloud_checksum=r["icloud_checksum"],
                )
            )
        return out

    # --- aggregates ---

    def bytes_freed_total(self, run_id: str | None = None) -> int:
        if run_id is None:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(size_bytes), 0) AS total FROM items WHERE state = ?",
                (ItemState.DELETED.value,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(i.size_bytes), 0) AS total FROM items i "
                "WHERE i.state = ? AND EXISTS ("
                "  SELECT 1 FROM item_events e "
                "  WHERE e.asset_id = i.asset_id AND e.run_id = ? AND e.to_state = ?"
                ")",
                (ItemState.DELETED.value, run_id, ItemState.DELETED.value),
            ).fetchone()
        return int(row["total"])

    def items_by_state(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT state, COUNT(*) AS n FROM items GROUP BY state"
        ).fetchall()
        return {r["state"]: int(r["n"]) for r in rows}

    def reset_items(self, asset_ids: list[str]) -> int:
        """Delete journal rows for *asset_ids* so they are treated as new again.

        Cascades to item_events. Touches only the journal — not iCloud. Returns
        the number of items actually removed (unknown ids are silently ignored).
        """
        removed = 0
        for asset_id in asset_ids:
            cur = self._conn.execute("DELETE FROM items WHERE asset_id = ?", (asset_id,))
            removed += cur.rowcount
        self._conn.commit()
        return removed

    def asset_ids_in_state(self, state: ItemState) -> list[str]:
        rows = self._conn.execute(
            "SELECT asset_id FROM items WHERE state = ?", (state.value,)
        ).fetchall()
        return [r["asset_id"] for r in rows]
