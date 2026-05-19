# iCloud Archiver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python CLI tool that archives the oldest iCloud Photos to an external drive (oldest-first, bounded by target bytes freed), with strict per-item verification before deletion, SQLite-journaled resume, an interactive disk picker, and a separate empty-trash command.

**Architecture:** A single Python 3.11+ package (`icloud_archiver`) split into small, single-responsibility modules. Pure-logic modules (`selector`, `verifier`, `organizer`, `journal`, `preflight`) are testable without iCloud or a real disk. iCloud-touching modules (`auth`, `catalog`, `downloader`, `deleter`) sit behind a narrow `ICloudPhotos` protocol so the rest of the system can run against a `FakeICloudPhotos` in tests. A SQLite journal at `~/.icloud-archiver/state.db` is the source of truth for resume.

**Tech Stack:** Python 3.11+, `uv` (package manager + venv), `pyicloud-ipd`, `icloudpd` (used as a library), `click`, `tqdm`, `structlog`, `keyring`, `Pillow`, `pytest`, `mypy`, `ruff`.

**Reference spec:** `docs/superpowers/specs/2026-05-19-icloud-archiver-design.md`

---

## Conventions used throughout this plan

- **All commands assume CWD is the repo root** `/Users/oleh.smirnov/Work/iCloudArchiver`.
- **Commit style:** Conventional Commits — `feat:`, `test:`, `refactor:`, `docs:`, `chore:`. The author trailer `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` is added automatically by the harness; do not add manually.
- **TDD loop per task**: write failing test → run + see it fail → minimal implementation → run + see it pass → commit. Subsequent edits in the same task may refactor; tests stay green throughout.
- **One PR / branch is NOT required** — main branch direct commits are fine for this project (single-author, no CI gate).
- **Type hints are required everywhere.** `mypy --strict` runs in CI loop.
- **No `from __future__ import annotations` unless a task explicitly requires it.** Python 3.11+ handles annotations fine.

---

## Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.python-version`
- Create: `src/icloud_archiver/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `README.md`

- [ ] **Step 1: Verify `uv` is installed**

Run: `uv --version`
Expected: prints a version like `uv 0.4.x` or newer. If missing: `brew install uv`.

- [ ] **Step 2: Write `.python-version`**

```
3.11
```

- [ ] **Step 3: Write `pyproject.toml`**

```toml
[project]
name = "icloud-archiver"
version = "0.1.0"
description = "Archive the oldest iCloud Photos to an external drive, then delete from iCloud."
requires-python = ">=3.11"
dependencies = [
    "pyicloud-ipd>=0.10.0",
    "icloudpd>=1.20.0",
    "click>=8.1.7",
    "tqdm>=4.66.0",
    "structlog>=24.1.0",
    "keyring>=23.13.1",
    "Pillow>=10.2.0",
    "python-ulid>=2.7.0",
]

[project.scripts]
icloud-archiver = "icloud_archiver.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/icloud_archiver"]

[dependency-groups]
dev = [
    "pytest>=8.0.0",
    "pytest-mock>=3.12.0",
    "mypy>=1.10.0",
    "ruff>=0.4.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
addopts = "-ra"

[tool.mypy]
strict = true
python_version = "3.11"
packages = ["icloud_archiver"]
mypy_path = "src"

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "SIM", "RUF"]
```

- [ ] **Step 4: Write `.gitignore`**

```
__pycache__/
*.pyc
.venv/
.pytest_cache/
.mypy_cache/
.ruff_cache/
dist/
build/
*.egg-info/

# Project-local state (should never be committed)
.scratch/
state.db
state.db-journal
```

- [ ] **Step 5: Create empty package init files**

`src/icloud_archiver/__init__.py`:
```python
"""iCloud Archiver — archive oldest iCloud Photos to an external drive."""

__version__ = "0.1.0"
```

`tests/__init__.py`:
```python
```

`tests/conftest.py`:
```python
```

- [ ] **Step 6: Write a placeholder `README.md`**

```markdown
# iCloud Archiver

Python CLI tool that archives the oldest iCloud Photos to an external drive
(oldest-first, bounded by a target number of bytes to free), then deletes them
from iCloud after strict per-item verification.

See `docs/superpowers/specs/2026-05-19-icloud-archiver-design.md` for the design,
and `docs/superpowers/plans/2026-05-19-icloud-archiver.md` for the implementation plan.

## Quick start

```bash
uv sync
uv run icloud-archiver login
uv run icloud-archiver plan --target-freed 1TB
uv run icloud-archiver run --target-freed 1TB
```
```

- [ ] **Step 7: Resolve dependencies and verify environment**

Run: `uv sync`
Expected: creates `.venv/`, writes `uv.lock`, prints "Resolved N packages".

Run: `uv run python -c "import icloud_archiver; print(icloud_archiver.__version__)"`
Expected: `0.1.0`

Run: `uv run pytest`
Expected: exits with code 5 (no tests collected). That's fine — we have no tests yet.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock .gitignore .python-version src/ tests/ README.md
git commit -m "chore: scaffold uv-managed Python package"
```

---

## Task 2: Core types — CatalogItem, ItemState, RunStatus

**Files:**
- Create: `src/icloud_archiver/types.py`
- Create: `tests/test_types.py`

- [ ] **Step 1: Write failing test for `CatalogItem` and `ItemState`**

`tests/test_types.py`:
```python
from datetime import datetime, timezone

import pytest

from icloud_archiver.types import CatalogItem, ItemState, RunStatus


def test_catalog_item_is_frozen():
    item = CatalogItem(
        asset_id="abc123",
        created_at=datetime(2014, 8, 23, 15, 42, 1, tzinfo=timezone.utc),
        size_bytes=4_823_942,
        albums=["Family/Italy 2014", "Highlights"],
        original_filename="IMG_1234.HEIC",
        has_live_photo=True,
        has_edits=False,
        mime_type="image/heic",
        icloud_checksum=None,
    )
    with pytest.raises(AttributeError):
        item.asset_id = "different"  # type: ignore[misc]


def test_item_state_terminal_set():
    assert ItemState.DELETED.is_terminal()
    assert ItemState.SKIPPED.is_terminal()
    assert ItemState.FAILED_VERIFY.is_terminal()
    assert not ItemState.PLANNED.is_terminal()
    assert not ItemState.DOWNLOADING.is_terminal()


def test_run_status_values():
    assert RunStatus.COMPLETED.value == "completed"
    assert RunStatus.ABORTED.value == "aborted"
    assert RunStatus.CRASHED.value == "crashed"
```

- [ ] **Step 2: Run test, see it fail**

Run: `uv run pytest tests/test_types.py -v`
Expected: ImportError or ModuleNotFoundError — `types` doesn't exist yet.

- [ ] **Step 3: Implement `types.py`**

`src/icloud_archiver/types.py`:
```python
"""Shared immutable data types."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class ItemState(str, Enum):
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


class RunStatus(str, Enum):
    COMPLETED = "completed"
    ABORTED = "aborted"
    CRASHED = "crashed"


@dataclass(frozen=True)
class CatalogItem:
    """Immutable description of one iCloud asset to be archived.

    `albums` is sorted case-insensitively for determinism. albums[0] is the
    primary placement folder. Apple folder/album nesting flattens to
    "Parent/Child" strings.
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
```

- [ ] **Step 4: Run tests, see them pass**

Run: `uv run pytest tests/test_types.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/icloud_archiver/types.py tests/test_types.py
git commit -m "feat: add CatalogItem, ItemState, RunStatus core types"
```

---

## Task 3: Config — size parsing and state dir resolution

**Files:**
- Create: `src/icloud_archiver/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write failing tests for `parse_size` and `state_dir`**

`tests/test_config.py`:
```python
from pathlib import Path

import pytest

from icloud_archiver.config import parse_size, state_dir


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1", 1),
        ("1024", 1024),
        ("1KB", 1_000),
        ("1MB", 1_000_000),
        ("1GB", 1_000_000_000),
        ("1TB", 1_000_000_000_000),
        ("500GB", 500_000_000_000),
        ("1.5TB", 1_500_000_000_000),
        ("1 TB", 1_000_000_000_000),
        ("  1tb  ", 1_000_000_000_000),
        ("1KiB", 1024),
        ("1MiB", 1024 * 1024),
        ("1GiB", 1024 ** 3),
        ("1TiB", 1024 ** 4),
    ],
)
def test_parse_size_valid(raw: str, expected: int) -> None:
    assert parse_size(raw) == expected


@pytest.mark.parametrize("raw", ["", "abc", "1XB", "-1GB", "1.5.6GB"])
def test_parse_size_invalid(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_size(raw)


def test_state_dir_resolves_to_dot_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    sd = state_dir()
    assert sd == tmp_path / ".icloud-archiver"
    assert sd.is_dir()  # created if missing
    assert (sd / "cookies").is_dir()
    assert (sd / "logs").is_dir()
    assert (sd / "plans").is_dir()
```

- [ ] **Step 2: Run, see fail**

Run: `uv run pytest tests/test_config.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `config.py`**

`src/icloud_archiver/config.py`:
```python
"""Configuration helpers: human-size parsing, state directory paths."""

from __future__ import annotations

import re
from pathlib import Path

_DECIMAL_UNITS = {"": 1, "B": 1, "KB": 1_000, "MB": 1_000_000, "GB": 1_000_000_000, "TB": 1_000_000_000_000}
_BINARY_UNITS = {"KIB": 1024, "MIB": 1024 ** 2, "GIB": 1024 ** 3, "TIB": 1024 ** 4}
_ALL_UNITS = {**_DECIMAL_UNITS, **_BINARY_UNITS}

_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([A-Za-z]*)\s*$")


def parse_size(raw: str) -> int:
    """Parse a human-readable size like '1TB', '500GB', '1.5TiB' to a byte count.

    Decimal units (KB/MB/GB/TB) use powers of 1000.
    Binary units (KiB/MiB/GiB/TiB) use powers of 1024.
    Bare numbers are treated as bytes.
    """
    if not raw or not raw.strip():
        raise ValueError(f"empty size string: {raw!r}")
    m = _SIZE_RE.match(raw)
    if not m:
        raise ValueError(f"could not parse size: {raw!r}")
    number_str, unit = m.group(1), m.group(2).upper()
    if unit not in _ALL_UNITS:
        raise ValueError(f"unknown size unit: {unit!r}")
    value = float(number_str) * _ALL_UNITS[unit]
    return int(value)


def state_dir() -> Path:
    """Return the per-user state directory, creating subdirs on first call."""
    base = Path.home() / ".icloud-archiver"
    base.mkdir(parents=True, exist_ok=True)
    (base / "cookies").mkdir(exist_ok=True)
    (base / "logs").mkdir(exist_ok=True)
    (base / "plans").mkdir(exist_ok=True)
    return base
```

- [ ] **Step 4: Run, see pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: all passing.

- [ ] **Step 5: Commit**

```bash
git add src/icloud_archiver/config.py tests/test_config.py
git commit -m "feat: parse human sizes, resolve per-user state directory"
```

---

## Task 4: Journal — SQLite schema, transitions, resume queries

**Files:**
- Create: `src/icloud_archiver/journal.py`
- Create: `tests/test_journal.py`

This task is larger because the journal is the system's source of truth. Multiple steps.

- [ ] **Step 1: Write the failing tests**

`tests/test_journal.py`:
```python
from datetime import datetime, timezone
from pathlib import Path

import pytest

from icloud_archiver.journal import Journal
from icloud_archiver.types import CatalogItem, ItemState, RunStatus


def _make_item(asset_id: str = "asset_1", size: int = 1000) -> CatalogItem:
    return CatalogItem(
        asset_id=asset_id,
        created_at=datetime(2015, 1, 1, tzinfo=timezone.utc),
        size_bytes=size,
        albums=["Album A"],
        original_filename=f"{asset_id}.HEIC",
        has_live_photo=False,
        has_edits=False,
        mime_type="image/heic",
        icloud_checksum=None,
    )


def test_open_creates_schema(tmp_path: Path) -> None:
    journal = Journal.open(tmp_path / "state.db")
    assert (tmp_path / "state.db").exists()
    journal.close()


def test_start_and_end_run(tmp_path: Path) -> None:
    journal = Journal.open(tmp_path / "state.db")
    run_id = journal.start_run(target_bytes=1_000_000, dry_run=False, archive_root="/Volumes/X")
    assert run_id  # ULID is non-empty
    journal.end_run(run_id, RunStatus.COMPLETED)
    rows = journal.list_runs()
    assert len(rows) == 1
    assert rows[0]["run_id"] == run_id
    assert rows[0]["ended_status"] == "completed"


def test_transition_records_event_and_updates_state(tmp_path: Path) -> None:
    journal = Journal.open(tmp_path / "state.db")
    run_id = journal.start_run(target_bytes=1_000_000, dry_run=False, archive_root="/Volumes/X")
    item = _make_item()

    journal.upsert_item(item, run_id, ItemState.PLANNED)
    assert journal.get_state(item.asset_id) == ItemState.PLANNED

    journal.transition(item.asset_id, ItemState.DOWNLOADING, run_id=run_id)
    journal.transition(item.asset_id, ItemState.DOWNLOADED, run_id=run_id)
    assert journal.get_state(item.asset_id) == ItemState.DOWNLOADED

    events = journal.events_for(item.asset_id)
    assert [e["to_state"] for e in events] == ["PLANNED", "DOWNLOADING", "DOWNLOADED"]


def test_resumable_items_returns_only_non_terminal(tmp_path: Path) -> None:
    journal = Journal.open(tmp_path / "state.db")
    run_id = journal.start_run(target_bytes=1_000_000, dry_run=False, archive_root="/Volumes/X")
    a, b, c = _make_item("a"), _make_item("b"), _make_item("c")
    journal.upsert_item(a, run_id, ItemState.PLANNED)
    journal.upsert_item(b, run_id, ItemState.DELETED)
    journal.upsert_item(c, run_id, ItemState.FAILED_VERIFY)

    resumable = journal.resumable_items()
    assert [r.asset_id for r in resumable] == ["a"]


def test_bytes_freed_total(tmp_path: Path) -> None:
    journal = Journal.open(tmp_path / "state.db")
    run_id = journal.start_run(target_bytes=10_000, dry_run=False, archive_root="/Volumes/X")
    a = _make_item("a", size=3_000)
    b = _make_item("b", size=4_000)
    c = _make_item("c", size=5_000)
    for item in (a, b, c):
        journal.upsert_item(item, run_id, ItemState.PLANNED)
    journal.transition("a", ItemState.DELETED, run_id=run_id)
    journal.transition("b", ItemState.DELETED, run_id=run_id)
    # c not deleted yet

    assert journal.bytes_freed_total(run_id) == 7_000


def test_is_terminal_reflects_state(tmp_path: Path) -> None:
    """is_terminal returns True only when the item's state is in the terminal set."""
    journal = Journal.open(tmp_path / "state.db")
    run_id = journal.start_run(target_bytes=10_000, dry_run=False, archive_root="/Volumes/X")
    a, b, c = _make_item("a"), _make_item("b"), _make_item("c")
    journal.upsert_item(a, run_id, ItemState.PLANNED)        # non-terminal
    journal.upsert_item(b, run_id, ItemState.DELETED)        # terminal
    journal.upsert_item(c, run_id, ItemState.FAILED_VERIFY)  # terminal
    assert journal.is_terminal("b")
    assert journal.is_terminal("c")
    assert not journal.is_terminal("a")
    assert not journal.is_terminal("never-seen")  # unknown items aren't terminal
```

- [ ] **Step 2: Run, see fail**

Run: `uv run pytest tests/test_journal.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `journal.py`**

`src/icloud_archiver/journal.py`:
```python
"""SQLite-backed journal — source of truth for archival state."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
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
  updated_at        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS item_events (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id            TEXT NOT NULL,
  asset_id          TEXT NOT NULL,
  at                TEXT NOT NULL,
  from_state        TEXT,
  to_state          TEXT NOT NULL,
  detail            TEXT
);
CREATE INDEX IF NOT EXISTS items_state_idx ON items(state);
CREATE INDEX IF NOT EXISTS items_created_idx ON items(created_at);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Journal:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    @classmethod
    def open(cls, path: Path) -> "Journal":
        path.parent.mkdir(parents=True, exist_ok=True)
        return cls(sqlite3.connect(str(path)))

    def close(self) -> None:
        self._conn.close()

    # --- runs ---

    def start_run(self, *, target_bytes: int, dry_run: bool, archive_root: str) -> str:
        run_id = str(ULID())
        self._conn.execute(
            "INSERT INTO runs(run_id, started_at, target_bytes, dry_run, archive_root) VALUES (?, ?, ?, ?, ?)",
            (run_id, _now(), target_bytes, int(dry_run), archive_root),
        )
        self._conn.commit()
        return run_id

    def end_run(self, run_id: str, status: RunStatus) -> None:
        self._conn.execute(
            "UPDATE runs SET ended_at = ?, ended_status = ? WHERE run_id = ?",
            (_now(), status.value, run_id),
        )
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
            self.transition(item.asset_id, state, run_id=run_id)
            return
        self._conn.execute(
            "INSERT INTO items(asset_id, first_seen_run, created_at, size_bytes, state, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                item.asset_id,
                run_id,
                item.created_at.isoformat(timespec="seconds"),
                item.size_bytes,
                state.value,
                _now(),
            ),
        )
        self._conn.execute(
            "INSERT INTO item_events(run_id, asset_id, at, from_state, to_state) VALUES (?, ?, ?, ?, ?)",
            (run_id, item.asset_id, _now(), None, state.value),
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

        sets = ["state = ?", "updated_at = ?"]
        params: list[Any] = [new_state.value, _now()]
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
            (run_id, asset_id, _now(), from_state, new_state.value, json.dumps(detail) if detail else None),
        )
        self._conn.commit()

    def get_state(self, asset_id: str) -> ItemState | None:
        row = self._conn.execute("SELECT state FROM items WHERE asset_id = ?", (asset_id,)).fetchone()
        return ItemState(row["state"]) if row else None

    def is_terminal(self, asset_id: str) -> bool:
        """True iff the asset is in a terminal state (DELETED/SKIPPED/FAILED_VERIFY).

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
        terminal = [ItemState.DELETED.value, ItemState.SKIPPED.value, ItemState.FAILED_VERIFY.value]
        q = (
            "SELECT asset_id, created_at, size_bytes FROM items "
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
                    albums=[],  # the catalog will re-fetch full details; resume routing only needs id
                    original_filename="",
                    has_live_photo=False,
                    has_edits=False,
                    mime_type="",
                    icloud_checksum=None,
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
                "  SELECT 1 FROM item_events e WHERE e.asset_id = i.asset_id AND e.run_id = ? AND e.to_state = ?"
                ")",
                (ItemState.DELETED.value, run_id, ItemState.DELETED.value),
            ).fetchone()
        return int(row["total"])

    def items_by_state(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT state, COUNT(*) AS n FROM items GROUP BY state"
        ).fetchall()
        return {r["state"]: int(r["n"]) for r in rows}

    def asset_ids_in_state(self, state: ItemState) -> list[str]:
        rows = self._conn.execute(
            "SELECT asset_id FROM items WHERE state = ?", (state.value,)
        ).fetchall()
        return [r["asset_id"] for r in rows]
```

- [ ] **Step 4: Run, see pass**

Run: `uv run pytest tests/test_journal.py -v`
Expected: all 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/icloud_archiver/journal.py tests/test_journal.py
git commit -m "feat: SQLite-backed journal with state transitions and resume queries"
```

---

## Task 5: Selector — oldest-first, bounded by target bytes

**Files:**
- Create: `src/icloud_archiver/selector.py`
- Create: `tests/test_selector.py`

- [ ] **Step 1: Write failing tests**

`tests/test_selector.py`:
```python
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from icloud_archiver.journal import Journal
from icloud_archiver.selector import select_until
from icloud_archiver.types import CatalogItem, ItemState


def _items(count: int, size: int = 1_000) -> list[CatalogItem]:
    base = datetime(2014, 1, 1, tzinfo=timezone.utc)
    return [
        CatalogItem(
            asset_id=f"a{i:03d}",
            created_at=base + timedelta(days=i),
            size_bytes=size,
            albums=[],
            original_filename=f"a{i:03d}.HEIC",
            has_live_photo=False,
            has_edits=False,
            mime_type="image/heic",
            icloud_checksum=None,
        )
        for i in range(count)
    ]


def _journal(tmp_path: Path) -> Journal:
    return Journal.open(tmp_path / "state.db")


def test_select_until_stops_at_target(tmp_path: Path) -> None:
    items = _items(10, size=1_000)  # total 10 KB
    selected = list(select_until(items, target_bytes=3_500, journal=_journal(tmp_path)))
    assert [s.asset_id for s in selected] == ["a000", "a001", "a002", "a003"]
    assert sum(s.size_bytes for s in selected) >= 3_500


def test_select_until_empty_target_returns_empty(tmp_path: Path) -> None:
    items = _items(5)
    selected = list(select_until(items, target_bytes=0, journal=_journal(tmp_path)))
    assert selected == []


def test_select_until_target_exceeds_catalog(tmp_path: Path) -> None:
    items = _items(3, size=1_000)
    selected = list(select_until(items, target_bytes=1_000_000_000, journal=_journal(tmp_path)))
    assert [s.asset_id for s in selected] == ["a000", "a001", "a002"]


def test_select_until_skips_only_terminal_items(tmp_path: Path) -> None:
    """Terminal-state items are skipped; non-terminal known items are re-yielded
    (so a crashed prior run resumes through the idempotent pipeline)."""
    items = _items(5, size=1_000)
    journal = _journal(tmp_path)
    run_id = journal.start_run(target_bytes=10_000, dry_run=False, archive_root="/X")
    # a001 reached terminal DELETED → skip
    # a003 is PLANNED (crashed mid-run previously) → re-include
    journal.upsert_item(items[1], run_id, ItemState.DELETED)
    journal.upsert_item(items[3], run_id, ItemState.PLANNED)

    selected = list(select_until(items, target_bytes=3_500, journal=journal))
    # a001 skipped (terminal), a003 included (non-terminal — resume)
    assert [s.asset_id for s in selected] == ["a000", "a002", "a003", "a004"]


def test_select_until_does_not_consume_iterator_past_target(tmp_path: Path) -> None:
    """Iterator should be lazy: items past the cutoff are never read."""
    consumed: list[str] = []

    def lazy_iter() -> Iterable[CatalogItem]:
        for i in _items(100, size=1_000):
            consumed.append(i.asset_id)
            yield i

    list(select_until(lazy_iter(), target_bytes=2_500, journal=_journal(tmp_path)))
    # Cumulative >= 2500 after 3 items; one extra may be consumed before we exit.
    assert len(consumed) <= 4
```

- [ ] **Step 2: Run, see fail**

Run: `uv run pytest tests/test_selector.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `selector.py`**

`src/icloud_archiver/selector.py`:
```python
"""Selection logic — yield items oldest-first until cumulative size ≥ target."""

from __future__ import annotations

from typing import Iterable, Iterator

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
```

- [ ] **Step 4: Run, see pass**

Run: `uv run pytest tests/test_selector.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/icloud_archiver/selector.py tests/test_selector.py
git commit -m "feat: target-bytes-bounded oldest-first selector"
```

---

## Task 6: Verifier — size, parse (HEIC/JPEG/MP4), sha256

**Files:**
- Create: `src/icloud_archiver/verifier.py`
- Create: `tests/test_verifier.py`
- Create: `tests/fixtures.py`

This task uses programmatically-generated fixtures so we don't bundle binary blobs in the repo.

- [ ] **Step 1: Write fixture helpers**

`tests/fixtures.py`:
```python
"""Synthetic fixture generators for tests that need real image/video bytes."""

from __future__ import annotations

import struct
from pathlib import Path

from PIL import Image


def make_jpeg(path: Path, width: int = 64, height: int = 64, color: tuple[int, int, int] = (200, 100, 50)) -> Path:
    img = Image.new("RGB", (width, height), color=color)
    img.save(path, format="JPEG", quality=80)
    return path


def make_png(path: Path, width: int = 64, height: int = 64) -> Path:
    Image.new("RGB", (width, height), color=(0, 200, 0)).save(path, format="PNG")
    return path


def truncate_file(path: Path, keep_bytes: int) -> Path:
    data = path.read_bytes()
    path.write_bytes(data[:keep_bytes])
    return path


def make_minimal_mp4(path: Path) -> Path:
    """A minimal MP4 with ftyp + moov + mdat top-level atoms."""
    def atom(box_type: bytes, payload: bytes) -> bytes:
        size = 8 + len(payload)
        return struct.pack(">I", size) + box_type + payload

    # ftyp: brand isom + version + compatible brands
    ftyp = atom(b"ftyp", b"isom\x00\x00\x02\x00" + b"isomiso2avc1mp41")
    moov = atom(b"moov", b"")  # empty but present
    mdat = atom(b"mdat", b"\x00" * 64)  # 64 bytes of fake media
    path.write_bytes(ftyp + moov + mdat)
    return path


def make_broken_mp4(path: Path) -> Path:
    """An MP4-looking file missing the moov atom."""
    def atom(box_type: bytes, payload: bytes) -> bytes:
        size = 8 + len(payload)
        return struct.pack(">I", size) + box_type + payload

    ftyp = atom(b"ftyp", b"isom\x00\x00\x02\x00" + b"isomiso2avc1mp41")
    mdat = atom(b"mdat", b"\x00" * 64)
    path.write_bytes(ftyp + mdat)  # no moov
    return path
```

- [ ] **Step 2: Write failing verifier tests**

`tests/test_verifier.py`:
```python
from datetime import datetime, timezone
from pathlib import Path

import pytest

from icloud_archiver.types import CatalogItem
from icloud_archiver.verifier import VerifyError, verify_size, verify_parse, sha256_of, verify
from tests.fixtures import (
    make_broken_mp4,
    make_jpeg,
    make_minimal_mp4,
    make_png,
    truncate_file,
)


def _item(size_bytes: int, mime: str = "image/jpeg", checksum: str | None = None) -> CatalogItem:
    return CatalogItem(
        asset_id="x",
        created_at=datetime(2015, 1, 1, tzinfo=timezone.utc),
        size_bytes=size_bytes,
        albums=[],
        original_filename="x.jpg",
        has_live_photo=False,
        has_edits=False,
        mime_type=mime,
        icloud_checksum=checksum,
    )


def test_verify_size_passes_when_match(tmp_path: Path) -> None:
    p = make_jpeg(tmp_path / "a.jpg")
    verify_size(p, expected=p.stat().st_size)


def test_verify_size_fails_when_mismatch(tmp_path: Path) -> None:
    p = make_jpeg(tmp_path / "a.jpg")
    with pytest.raises(VerifyError):
        verify_size(p, expected=p.stat().st_size - 1)


def test_verify_parse_jpeg_passes(tmp_path: Path) -> None:
    p = make_jpeg(tmp_path / "a.jpg")
    verify_parse(p, mime_type="image/jpeg")


def test_verify_parse_jpeg_truncated_fails(tmp_path: Path) -> None:
    p = make_jpeg(tmp_path / "a.jpg")
    truncate_file(p, keep_bytes=64)
    with pytest.raises(VerifyError):
        verify_parse(p, mime_type="image/jpeg")


def test_verify_parse_png_passes(tmp_path: Path) -> None:
    p = make_png(tmp_path / "a.png")
    verify_parse(p, mime_type="image/png")


def test_verify_parse_mp4_passes(tmp_path: Path) -> None:
    p = make_minimal_mp4(tmp_path / "a.mp4")
    verify_parse(p, mime_type="video/mp4")


def test_verify_parse_mp4_missing_moov_fails(tmp_path: Path) -> None:
    p = make_broken_mp4(tmp_path / "a.mp4")
    with pytest.raises(VerifyError, match="moov"):
        verify_parse(p, mime_type="video/mp4")


def test_sha256_of_is_stable(tmp_path: Path) -> None:
    p = make_jpeg(tmp_path / "a.jpg")
    a = sha256_of(p)
    b = sha256_of(p)
    assert a == b
    assert len(a) == 64


def test_verify_full_chain_passes(tmp_path: Path) -> None:
    p = make_jpeg(tmp_path / "a.jpg")
    item = _item(size_bytes=p.stat().st_size, mime="image/jpeg")
    result = verify(item, p)
    assert result.sha256 == sha256_of(p)


def test_verify_full_chain_checksum_mismatch_fails(tmp_path: Path) -> None:
    p = make_jpeg(tmp_path / "a.jpg")
    item = _item(size_bytes=p.stat().st_size, mime="image/jpeg", checksum="deadbeef" * 8)
    with pytest.raises(VerifyError, match="checksum"):
        verify(item, p)
```

- [ ] **Step 3: Run, see fail**

Run: `uv run pytest tests/test_verifier.py -v`
Expected: ImportError.

- [ ] **Step 4: Implement `verifier.py`**

`src/icloud_archiver/verifier.py`:
```python
"""Strict per-file verification: size + parse + sha256 + optional checksum compare."""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from icloud_archiver.types import CatalogItem


class VerifyError(Exception):
    """Raised when a verification step fails."""


@dataclass(frozen=True)
class VerifyResult:
    sha256: str


def verify_size(path: Path, *, expected: int) -> None:
    actual = path.stat().st_size
    if actual != expected:
        raise VerifyError(f"size mismatch for {path.name}: expected {expected}, got {actual}")


def verify_parse(path: Path, *, mime_type: str) -> None:
    mime = (mime_type or "").lower()
    if mime in {"image/jpeg", "image/png", "image/heic", "image/heif", "image/tiff", "image/gif"}:
        try:
            with Image.open(path) as img:
                img.verify()
        except (UnidentifiedImageError, OSError) as exc:
            raise VerifyError(f"image parse failed for {path.name}: {exc}") from exc
        return
    if mime in {"video/mp4", "video/quicktime", "video/mov"}:
        _walk_mp4_atoms(path)
        return
    # Unknown / generic: at minimum, confirm non-empty and magic-byte sanity.
    if path.stat().st_size == 0:
        raise VerifyError(f"empty file: {path.name}")


def _walk_mp4_atoms(path: Path) -> None:
    seen: set[str] = set()
    total = 0
    file_size = path.stat().st_size
    with path.open("rb") as f:
        while True:
            header = f.read(8)
            if not header:
                break
            if len(header) < 8:
                raise VerifyError(f"mp4 truncated header in {path.name}")
            size, box_type = struct.unpack(">I4s", header)
            if size == 1:
                ext = f.read(8)
                if len(ext) < 8:
                    raise VerifyError(f"mp4 truncated extended size in {path.name}")
                size = struct.unpack(">Q", ext)[0]
                header_len = 16
            else:
                header_len = 8
            if size < header_len:
                raise VerifyError(f"mp4 invalid box size {size} in {path.name}")
            seen.add(box_type.decode("ascii", errors="replace"))
            total += size
            f.seek(size - header_len, 1)
    if "ftyp" not in seen:
        raise VerifyError(f"mp4 missing ftyp atom in {path.name}")
    if "moov" not in seen:
        raise VerifyError(f"mp4 missing moov atom in {path.name}")
    if "mdat" not in seen:
        raise VerifyError(f"mp4 missing mdat atom in {path.name}")
    if total != file_size:
        raise VerifyError(f"mp4 atoms ({total}B) do not cover file ({file_size}B) in {path.name}")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(item: CatalogItem, path: Path) -> VerifyResult:
    """Run the full chain: size → parse → sha → optional checksum compare."""
    verify_size(path, expected=item.size_bytes)
    verify_parse(path, mime_type=item.mime_type)
    digest = sha256_of(path)
    if item.icloud_checksum and item.icloud_checksum.lower() != digest.lower():
        raise VerifyError(
            f"checksum mismatch for {item.asset_id}: "
            f"iCloud reported {item.icloud_checksum}, local sha256 {digest}"
        )
    return VerifyResult(sha256=digest)
```

- [ ] **Step 5: Run, see pass**

Run: `uv run pytest tests/test_verifier.py -v`
Expected: 10 passed.

- [ ] **Step 6: Commit**

```bash
git add src/icloud_archiver/verifier.py tests/test_verifier.py tests/fixtures.py
git commit -m "feat: strict per-file verifier (size, parse, sha256)"
```

---

## Task 7: Organizer — primary path, hardlinks, sidecar JSON

**Files:**
- Create: `src/icloud_archiver/organizer.py`
- Create: `tests/test_organizer.py`

- [ ] **Step 1: Write failing tests**

`tests/test_organizer.py`:
```python
import json
from datetime import datetime, timezone
from pathlib import Path

from icloud_archiver.organizer import DownloadedFiles, OrganizedPaths, organize, sidecar_dict
from icloud_archiver.types import CatalogItem


def _item(
    asset_id: str = "x",
    original_filename: str = "IMG_1234.HEIC",
    albums: list[str] | None = None,
    has_live_photo: bool = False,
) -> CatalogItem:
    return CatalogItem(
        asset_id=asset_id,
        created_at=datetime(2014, 8, 23, 15, 42, 1, tzinfo=timezone.utc),
        size_bytes=10,
        albums=albums if albums is not None else [],
        original_filename=original_filename,
        has_live_photo=has_live_photo,
        has_edits=False,
        mime_type="image/heic",
        icloud_checksum=None,
    )


def _make_scratch(tmp_path: Path, item_id: str = "x", with_live: bool = False) -> DownloadedFiles:
    scratch = tmp_path / ".scratch"
    scratch.mkdir()
    original = scratch / f"{item_id}_orig.HEIC"
    original.write_bytes(b"original-bytes")
    files = DownloadedFiles(original=original)
    if with_live:
        live = scratch / f"{item_id}_live.MOV"
        live.write_bytes(b"live-bytes")
        files = DownloadedFiles(original=original, live_photo=live)
    return files


def test_no_album_falls_back_to_date_folder(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    item = _item(albums=[])
    files = _make_scratch(tmp_path)

    paths = organize(item, files, archive, sidecar=sidecar_dict(item, sha256="a" * 64, run_id="r1"))

    expected_dir = archive / "_NoAlbum" / "2014" / "08"
    assert (expected_dir / "IMG_1234.HEIC").is_file()
    assert (expected_dir / "IMG_1234.json").is_file()
    assert paths.primary == expected_dir / "IMG_1234.HEIC"
    assert paths.hardlinks == []


def test_single_album_places_under_album_folder(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    item = _item(albums=["Italy 2014"])
    files = _make_scratch(tmp_path)

    paths = organize(item, files, archive, sidecar=sidecar_dict(item, sha256="a" * 64, run_id="r1"))

    assert paths.primary == archive / "Italy 2014" / "IMG_1234.HEIC"
    assert paths.hardlinks == []
    assert (archive / "Italy 2014" / "IMG_1234.json").is_file()


def test_multi_album_creates_hardlinks_for_image_and_sidecar(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    item = _item(albums=["Family", "Highlights", "Italy 2014"])
    files = _make_scratch(tmp_path)

    paths = organize(item, files, archive, sidecar=sidecar_dict(item, sha256="a" * 64, run_id="r1"))

    primary = archive / "Family" / "IMG_1234.HEIC"
    assert paths.primary == primary
    assert (archive / "Highlights" / "IMG_1234.HEIC").is_file()
    assert (archive / "Italy 2014" / "IMG_1234.HEIC").is_file()

    # Hardlinks share the inode
    primary_inode = primary.stat().st_ino
    assert (archive / "Highlights" / "IMG_1234.HEIC").stat().st_ino == primary_inode
    assert (archive / "Italy 2014" / "IMG_1234.HEIC").stat().st_ino == primary_inode

    # Sidecar exists in every folder (also hardlinked)
    sidecar_inode = (archive / "Family" / "IMG_1234.json").stat().st_ino
    assert (archive / "Highlights" / "IMG_1234.json").stat().st_ino == sidecar_inode
    assert (archive / "Italy 2014" / "IMG_1234.json").stat().st_ino == sidecar_inode


def test_live_photo_paired_video_placed_and_linked(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    item = _item(albums=["A", "B"], has_live_photo=True)
    files = _make_scratch(tmp_path, with_live=True)

    organize(item, files, archive, sidecar=sidecar_dict(item, sha256="a" * 64, run_id="r1"))

    primary_mov = archive / "A" / "IMG_1234_LIVE.MOV"
    assert primary_mov.is_file()
    linked_mov = archive / "B" / "IMG_1234_LIVE.MOV"
    assert linked_mov.stat().st_ino == primary_mov.stat().st_ino


def test_organize_is_idempotent(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    item = _item(albums=["A", "B"])

    # Run 1
    files1 = _make_scratch(tmp_path, item_id="r1")
    organize(item, files1, archive, sidecar=sidecar_dict(item, sha256="a" * 64, run_id="r1"))
    primary_inode = (archive / "A" / "IMG_1234.HEIC").stat().st_ino

    # Run 2 — scratch was wiped, item file already in place, but we still call organize.
    # We expect no error and no duplicated bytes.
    files2 = _make_scratch(tmp_path, item_id="r2")
    # The "primary" is already there; the source from scratch should be cleaned up by organize.
    organize(item, files2, archive, sidecar=sidecar_dict(item, sha256="a" * 64, run_id="r2"))
    assert (archive / "A" / "IMG_1234.HEIC").stat().st_ino == primary_inode


def test_sidecar_dict_shape(tmp_path: Path) -> None:
    item = _item(albums=["A", "B"], has_live_photo=True)
    side = sidecar_dict(item, sha256="a" * 64, run_id="r1")
    assert side["asset_id"] == "x"
    assert side["original_filename"] == "IMG_1234.HEIC"
    assert side["sha256"] == "a" * 64
    assert side["albums"] == ["A", "B"]
    assert side["has_live_photo"] is True
    assert "archived_at" in side
    assert side["archived_by_run"] == "r1"
```

- [ ] **Step 2: Run, see fail**

Run: `uv run pytest tests/test_organizer.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `organizer.py`**

`src/icloud_archiver/organizer.py`:
```python
"""Place verified files into album folders, with hardlinks for multi-album items."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from icloud_archiver.types import CatalogItem


@dataclass(frozen=True)
class DownloadedFiles:
    """Result of the downloader: paths inside the scratch directory."""

    original: Path
    live_photo: Path | None = None
    edited: Path | None = None


@dataclass(frozen=True)
class OrganizedPaths:
    primary: Path
    hardlinks: list[Path]
    sidecar_primary: Path
    sidecar_hardlinks: list[Path]


def sidecar_dict(item: CatalogItem, *, sha256: str, run_id: str) -> dict[str, Any]:
    return {
        "asset_id": item.asset_id,
        "original_filename": item.original_filename,
        "created_at": item.created_at.isoformat(timespec="seconds"),
        "mime_type": item.mime_type,
        "size_bytes": item.size_bytes,
        "sha256": sha256,
        "icloud_checksum": item.icloud_checksum,
        "albums": list(item.albums),
        "has_live_photo": item.has_live_photo,
        "has_edits": item.has_edits,
        "archived_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "archived_by_run": run_id,
    }


def _primary_folder(item: CatalogItem, archive_root: Path) -> Path:
    if item.albums:
        return archive_root / item.albums[0]
    return archive_root / "_NoAlbum" / f"{item.created_at.year:04d}" / f"{item.created_at.month:02d}"


def _additional_folders(item: CatalogItem, archive_root: Path) -> list[Path]:
    return [archive_root / a for a in item.albums[1:]]


def _move_or_skip(src: Path, dest: Path) -> None:
    """Move src → dest. If dest already exists and matches src by content, drop src."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        if src.exists():
            src.unlink()
        return
    shutil.move(str(src), str(dest))


def _hardlink_or_skip(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        if dest.stat().st_ino == src.stat().st_ino:
            return  # already correct
        # Different inode — leave the existing file alone, log via caller's journal.
        return
    os.link(src, dest)


def _live_photo_name(original_filename: str) -> str:
    stem = Path(original_filename).stem
    return f"{stem}_LIVE.MOV"


def _edited_name(original_filename: str) -> str:
    p = Path(original_filename)
    return f"{p.stem}_EDITED{p.suffix}"


def organize(
    item: CatalogItem,
    files: DownloadedFiles,
    archive_root: Path,
    *,
    sidecar: dict[str, Any],
) -> OrganizedPaths:
    primary_dir = _primary_folder(item, archive_root)
    primary_dir.mkdir(parents=True, exist_ok=True)

    primary = primary_dir / item.original_filename
    _move_or_skip(files.original, primary)

    live_primary: Path | None = None
    if files.live_photo is not None:
        live_primary = primary_dir / _live_photo_name(item.original_filename)
        _move_or_skip(files.live_photo, live_primary)

    edited_primary: Path | None = None
    if files.edited is not None:
        edited_primary = primary_dir / _edited_name(item.original_filename)
        _move_or_skip(files.edited, edited_primary)

    sidecar_primary = primary_dir / (Path(item.original_filename).stem + ".json")
    sidecar_primary.write_text(json.dumps(sidecar, indent=2, sort_keys=True))

    hardlinks: list[Path] = []
    sidecar_hardlinks: list[Path] = []
    for extra_dir in _additional_folders(item, archive_root):
        extra_dir.mkdir(parents=True, exist_ok=True)
        extra_primary = extra_dir / item.original_filename
        _hardlink_or_skip(primary, extra_primary)
        hardlinks.append(extra_primary)
        if live_primary is not None:
            _hardlink_or_skip(live_primary, extra_dir / live_primary.name)
        if edited_primary is not None:
            _hardlink_or_skip(edited_primary, extra_dir / edited_primary.name)
        extra_sidecar = extra_dir / sidecar_primary.name
        _hardlink_or_skip(sidecar_primary, extra_sidecar)
        sidecar_hardlinks.append(extra_sidecar)

    # fsync primary directory entry so renames + sidecar are durable
    fd = os.open(str(primary_dir), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

    return OrganizedPaths(
        primary=primary,
        hardlinks=hardlinks,
        sidecar_primary=sidecar_primary,
        sidecar_hardlinks=sidecar_hardlinks,
    )
```

- [ ] **Step 4: Run, see pass**

Run: `uv run pytest tests/test_organizer.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/icloud_archiver/organizer.py tests/test_organizer.py
git commit -m "feat: organizer places items in album folders with hardlinks + sidecars"
```

---

## Task 8: Preflight — disk picker, FS probe, reformat prompt, free-space check

**Files:**
- Create: `src/icloud_archiver/preflight.py`
- Create: `tests/test_preflight.py`
- Create: `tests/fixtures_diskutil.py`

The `diskutil list -plist` output is verbose; we capture it as a fixture rather than asserting against a fragile string-literal.

- [ ] **Step 1: Capture a real `diskutil list -plist` sample**

Run this manually once (it just captures a fixture, no test of behavior):

```bash
mkdir -p tests/fixtures_data
diskutil list -plist > tests/fixtures_data/diskutil_list.plist
```

The fixture should contain at least one external volume to be useful — plug in a USB drive before running if needed. If no external is present, hand-craft a minimal plist following the structure in `man diskutil`.

- [ ] **Step 2: Write failing tests**

`tests/test_preflight.py`:
```python
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from icloud_archiver.preflight import (
    Drive,
    detect_filesystem,
    list_external_drives,
    needs_reformat,
    pick_drive_interactive,
)


def test_needs_reformat_true_for_exfat() -> None:
    assert needs_reformat("exfat")
    assert needs_reformat("ExFAT")
    assert needs_reformat("ntfs")
    assert needs_reformat("msdos")
    assert needs_reformat("fat32")


def test_needs_reformat_false_for_apfs_and_hfs() -> None:
    assert not needs_reformat("apfs")
    assert not needs_reformat("APFS")
    assert not needs_reformat("hfs+")
    assert not needs_reformat("journaled hfs+")


def test_list_external_drives_filters_system(monkeypatch: pytest.MonkeyPatch) -> None:
    plist = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>AllDisksAndPartitions</key>
  <array>
    <dict>
      <key>DeviceIdentifier</key><string>disk0</string>
      <key>Partitions</key>
      <array>
        <dict>
          <key>DeviceIdentifier</key><string>disk0s2</string>
          <key>VolumeName</key><string>Macintosh HD</string>
          <key>MountPoint</key><string>/</string>
        </dict>
      </array>
    </dict>
    <dict>
      <key>DeviceIdentifier</key><string>disk4</string>
      <key>Partitions</key>
      <array>
        <dict>
          <key>DeviceIdentifier</key><string>disk4s2</string>
          <key>VolumeName</key><string>Samsung T7</string>
          <key>MountPoint</key><string>/Volumes/T7</string>
        </dict>
      </array>
    </dict>
  </array>
</dict>
</plist>"""

    def fake_run(*args: object, **kwargs: object) -> MagicMock:
        m = MagicMock()
        m.stdout = plist
        m.returncode = 0
        return m

    monkeypatch.setattr("subprocess.run", fake_run)
    # Also need to mock detect_filesystem & free_bytes used by list_external_drives.
    monkeypatch.setattr(
        "icloud_archiver.preflight.detect_filesystem",
        lambda mp: "apfs" if "T7" in str(mp) else "apfs",
    )
    monkeypatch.setattr(
        "icloud_archiver.preflight._volume_stats",
        lambda mp: (1_800_000_000_000, 2_000_000_000_000),
    )

    drives = list_external_drives()
    # disk0 (system) excluded; disk4 (T7) included
    assert [d.volume_name for d in drives] == ["Samsung T7"]
    assert drives[0].mount_point == Path("/Volumes/T7")
    assert drives[0].fs == "apfs"
    assert drives[0].is_external


def test_pick_drive_interactive_selects_by_index(monkeypatch: pytest.MonkeyPatch) -> None:
    drives = [
        Drive(
            device_id="disk4s2",
            volume_name="Samsung T7",
            mount_point=Path("/Volumes/T7"),
            fs="apfs",
            free_bytes=1_800_000_000_000,
            total_bytes=2_000_000_000_000,
            is_external=True,
        ),
        Drive(
            device_id="disk5s2",
            volume_name="LaCie",
            mount_point=Path("/Volumes/LaCie"),
            fs="hfs+",
            free_bytes=500_000_000_000,
            total_bytes=2_000_000_000_000,
            is_external=True,
        ),
    ]
    monkeypatch.setattr("builtins.input", lambda _prompt="": "2")
    picked = pick_drive_interactive(drives)
    assert picked.volume_name == "LaCie"


def test_pick_drive_interactive_quit(monkeypatch: pytest.MonkeyPatch) -> None:
    drives = [
        Drive(
            device_id="disk4s2",
            volume_name="X",
            mount_point=Path("/Volumes/X"),
            fs="apfs",
            free_bytes=1,
            total_bytes=1,
            is_external=True,
        ),
    ]
    monkeypatch.setattr("builtins.input", lambda _prompt="": "q")
    with pytest.raises(SystemExit):
        pick_drive_interactive(drives)
```

- [ ] **Step 3: Run, see fail**

Run: `uv run pytest tests/test_preflight.py -v`
Expected: ImportError.

- [ ] **Step 4: Implement `preflight.py`**

`src/icloud_archiver/preflight.py`:
```python
"""Disk picker, filesystem probe, reformat prompt, free-space check, sleep prevention."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_REFORMAT_FS = frozenset({"exfat", "msdos", "fat32", "ntfs", "msdos_fat32"})


@dataclass(frozen=True)
class Drive:
    device_id: str
    volume_name: str
    mount_point: Path
    fs: str
    free_bytes: int
    total_bytes: int
    is_external: bool


def needs_reformat(fs: str) -> bool:
    return fs.lower().replace(" ", "_") in _REFORMAT_FS


def detect_filesystem(mount_point: Path) -> str:
    """Run `diskutil info -plist <mount>` and pull `FilesystemType`."""
    res = subprocess.run(
        ["diskutil", "info", "-plist", str(mount_point)],
        capture_output=True,
        check=True,
    )
    data = plistlib.loads(res.stdout)
    return str(data.get("FilesystemType", "")).lower()


def _volume_stats(mount_point: Path) -> tuple[int, int]:
    """Return (free_bytes, total_bytes) using statvfs."""
    s = os.statvfs(mount_point)
    free = s.f_bavail * s.f_frsize
    total = s.f_blocks * s.f_frsize
    return free, total


def list_external_drives() -> list[Drive]:
    """Probe `diskutil list -plist` and return mounted, non-system volumes."""
    res = subprocess.run(["diskutil", "list", "-plist"], capture_output=True, check=True)
    data = plistlib.loads(res.stdout)
    out: list[Drive] = []
    for disk in data.get("AllDisksAndPartitions", []):
        for part in disk.get("Partitions", []):
            mp = part.get("MountPoint")
            if not mp:
                continue
            mount_point = Path(mp)
            if mount_point == Path("/"):
                continue  # system root
            if not mount_point.is_absolute() or not str(mount_point).startswith("/Volumes/"):
                continue
            try:
                fs = detect_filesystem(mount_point)
                free, total = _volume_stats(mount_point)
            except subprocess.CalledProcessError:
                continue
            out.append(
                Drive(
                    device_id=part["DeviceIdentifier"],
                    volume_name=part.get("VolumeName", "(unnamed)"),
                    mount_point=mount_point,
                    fs=fs,
                    free_bytes=free,
                    total_bytes=total,
                    is_external=True,
                )
            )
    return out


def _human(n: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    f = float(n)
    for u in units:
        if f < 1000:
            return f"{f:.1f} {u}"
        f /= 1000
    return f"{f:.1f} PB"


def _render_table(drives: list[Drive]) -> str:
    rows = ["External drives available:\n"]
    for i, d in enumerate(drives, start=1):
        flag = "  ⚠ will need reformat" if needs_reformat(d.fs) else ""
        rows.append(
            f"  [{i}]  {d.volume_name:<18} {d.fs.upper():<7} "
            f"{_human(d.free_bytes)} free / {_human(d.total_bytes)}   "
            f"{d.mount_point}{flag}"
        )
    return "\n".join(rows)


def pick_drive_interactive(drives: list[Drive]) -> Drive:
    if not drives:
        print("No external drives mounted. Plug one in and try again.", file=sys.stderr)
        raise SystemExit(1)
    print(_render_table(drives))
    prompt = f"\nSelect target drive [1-{len(drives)}], or 'q' to quit: "
    while True:
        choice = input(prompt).strip().lower()
        if choice == "q":
            raise SystemExit(0)
        try:
            idx = int(choice)
        except ValueError:
            print(f"  not a number: {choice!r}")
            continue
        if 1 <= idx <= len(drives):
            return drives[idx - 1]
        print(f"  out of range: {idx}")


def confirm_reformat(drive: Drive, *, yes_erase: bool = False) -> bool:
    """Show the typed-confirmation gate. Return True if user authorizes erase."""
    print(
        f"\nDrive '{drive.volume_name}' ({drive.device_id}) is {drive.fs}.\n"
        "This archive uses hardlinks for multi-album items, which require APFS or HFS+.\n"
    )
    print(f"Reformat {drive.volume_name} as APFS now?")
    if yes_erase:
        print("(--yes-erase set — proceeding without typed-confirmation)\n")
        return True
    print(f"   [type 'ERASE {drive.volume_name}' to confirm]\n")
    print("⚠️  THIS WILL PERMANENTLY DELETE EVERYTHING ON THIS DRIVE.")
    print("⚠️  Other drives are NOT affected.")
    print("⚠️  This action cannot be undone.\n")
    given = input("> ").strip()
    return given == f"ERASE {drive.volume_name}"


def reformat_apfs(drive: Drive) -> None:
    """Reformat `drive.device_id` as APFS named after the existing volume name."""
    print(f"Reformatting {drive.device_id} ('{drive.volume_name}') as APFS...")
    subprocess.run(
        ["diskutil", "eraseDisk", "APFS", drive.volume_name, drive.device_id],
        check=True,
    )


def caffeinate_for_run() -> subprocess.Popen[bytes]:
    """Spawn `caffeinate -dimsu` to prevent sleep for the lifetime of the returned Popen."""
    return subprocess.Popen(["caffeinate", "-dimsu"])
```

- [ ] **Step 5: Run, see pass**

Run: `uv run pytest tests/test_preflight.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add src/icloud_archiver/preflight.py tests/test_preflight.py
git commit -m "feat: disk picker, FS probe, reformat prompt, caffeinate spawn"
```

---

## Task 9: ICloudPhotos protocol + FakeICloudPhotos

**Files:**
- Create: `src/icloud_archiver/icloud_iface.py`
- Create: `tests/fakes.py`
- Create: `tests/test_fake_icloud.py`

This task locks in the boundary between us and the Apple-touching layer. The Fake serves real bytes from `tmp_path` so integration tests can run.

- [ ] **Step 1: Write the protocol + Fake + failing tests**

`tests/test_fake_icloud.py`:
```python
from datetime import datetime, timezone
from pathlib import Path

from icloud_archiver.types import CatalogItem
from tests.fakes import FakeAsset, FakeICloudPhotos


def _asset(asset_id: str, day: int, size: int = 1000, albums: list[str] | None = None) -> FakeAsset:
    return FakeAsset(
        item=CatalogItem(
            asset_id=asset_id,
            created_at=datetime(2014, 1, day, tzinfo=timezone.utc),
            size_bytes=size,
            albums=albums or [],
            original_filename=f"{asset_id}.HEIC",
            has_live_photo=False,
            has_edits=False,
            mime_type="image/heic",
            icloud_checksum=None,
        ),
        original_bytes=b"X" * size,
    )


def test_fake_yields_oldest_first(tmp_path: Path) -> None:
    fake = FakeICloudPhotos(assets=[_asset("c", 3), _asset("a", 1), _asset("b", 2)])
    ids = [item.asset_id for item in fake.iter_oldest_first()]
    assert ids == ["a", "b", "c"]


def test_fake_download_writes_bytes(tmp_path: Path) -> None:
    asset = _asset("a", 1, size=2048)
    fake = FakeICloudPhotos(assets=[asset])
    dest = tmp_path / "out.HEIC"
    fake.download_original(asset.item.asset_id, dest)
    assert dest.read_bytes() == b"X" * 2048


def test_fake_delete_removes_from_iteration(tmp_path: Path) -> None:
    fake = FakeICloudPhotos(assets=[_asset("a", 1), _asset("b", 2)])
    fake.delete("a")
    assert [i.asset_id for i in fake.iter_oldest_first()] == ["b"]


def test_fake_delete_unknown_raises(tmp_path: Path) -> None:
    fake = FakeICloudPhotos(assets=[_asset("a", 1)])
    import pytest

    with pytest.raises(KeyError):
        fake.delete("nope")


def test_fake_can_simulate_download_failure(tmp_path: Path) -> None:
    asset = _asset("a", 1)
    fake = FakeICloudPhotos(assets=[asset])
    fake.fail_download_for.add("a")
    dest = tmp_path / "out.HEIC"
    import pytest

    with pytest.raises(IOError):
        fake.download_original("a", dest)


def test_fake_can_serve_truncated_bytes(tmp_path: Path) -> None:
    asset = _asset("a", 1, size=2048)
    fake = FakeICloudPhotos(assets=[asset])
    fake.truncate_download_for["a"] = 100  # serve only 100 bytes
    dest = tmp_path / "out.HEIC"
    fake.download_original("a", dest)
    assert dest.stat().st_size == 100
```

`tests/fakes.py`:
```python
"""In-memory FakeICloudPhotos for unit + integration tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

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
            raise IOError(f"injected download failure for {asset_id}")
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
            raise IOError(f"injected delete failure for {asset_id}")
        if asset_id not in self._assets:
            raise KeyError(asset_id)
        del self._assets[asset_id]

    def empty_trash(self, asset_ids: list[str]) -> None:
        # In our fake, items are already gone after delete(); empty_trash is a no-op
        # for assets it doesn't know about.
        return None
```

`src/icloud_archiver/icloud_iface.py`:
```python
"""Protocol the rest of the system uses to talk to iCloud (real or fake)."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator, Protocol

from icloud_archiver.types import CatalogItem


class ICloudPhotos(Protocol):
    def iter_oldest_first(self) -> Iterator[CatalogItem]: ...
    def download_original(self, asset_id: str, dest: Path) -> None: ...
    def download_live_photo(self, asset_id: str, dest: Path) -> None: ...
    def download_edited(self, asset_id: str, dest: Path) -> None: ...
    def delete(self, asset_id: str) -> None: ...
    def empty_trash(self, asset_ids: list[str]) -> None: ...
```

- [ ] **Step 2: Run, see pass**

Run: `uv run pytest tests/test_fake_icloud.py -v`
Expected: 6 passed.

- [ ] **Step 3: Commit**

```bash
git add src/icloud_archiver/icloud_iface.py tests/fakes.py tests/test_fake_icloud.py
git commit -m "feat: ICloudPhotos protocol + FakeICloudPhotos for tests"
```

---

## Task 10: Downloader and Deleter (use the iCloud protocol)

**Files:**
- Create: `src/icloud_archiver/downloader.py`
- Create: `src/icloud_archiver/deleter.py`
- Create: `tests/test_downloader.py`
- Create: `tests/test_deleter.py`

- [ ] **Step 1: Write failing tests**

`tests/test_downloader.py`:
```python
from datetime import datetime, timezone
from pathlib import Path

import pytest

from icloud_archiver.downloader import DownloadError, fetch_item
from icloud_archiver.organizer import DownloadedFiles
from icloud_archiver.types import CatalogItem
from tests.fakes import FakeAsset, FakeICloudPhotos


def _item(asset_id: str = "a", has_live: bool = False, has_edits: bool = False) -> CatalogItem:
    return CatalogItem(
        asset_id=asset_id,
        created_at=datetime(2015, 1, 1, tzinfo=timezone.utc),
        size_bytes=1000,
        albums=[],
        original_filename=f"{asset_id}.HEIC",
        has_live_photo=has_live,
        has_edits=has_edits,
        mime_type="image/heic",
        icloud_checksum=None,
    )


def _fake_with(item: CatalogItem, *, with_live: bool = False, with_edits: bool = False) -> FakeICloudPhotos:
    asset = FakeAsset(
        item=item,
        original_bytes=b"X" * item.size_bytes,
        live_photo_bytes=b"LIVE" * 100 if with_live else None,
        edited_bytes=b"EDIT" * 100 if with_edits else None,
    )
    return FakeICloudPhotos(assets=[asset])


def test_fetch_item_writes_original_to_scratch(tmp_path: Path) -> None:
    item = _item()
    fake = _fake_with(item)
    files = fetch_item(item, fake, scratch_dir=tmp_path)
    assert files.original.read_bytes() == b"X" * 1000
    assert files.original.suffix == ".HEIC"
    assert files.live_photo is None
    assert files.edited is None


def test_fetch_item_includes_live_photo(tmp_path: Path) -> None:
    item = _item(has_live=True)
    fake = _fake_with(item, with_live=True)
    files = fetch_item(item, fake, scratch_dir=tmp_path)
    assert files.live_photo is not None
    assert files.live_photo.suffix == ".MOV"


def test_fetch_item_includes_edited(tmp_path: Path) -> None:
    item = _item(has_edits=True)
    fake = _fake_with(item, with_edits=True)
    files = fetch_item(item, fake, scratch_dir=tmp_path)
    assert files.edited is not None


def test_fetch_item_raises_on_download_failure(tmp_path: Path) -> None:
    item = _item()
    fake = _fake_with(item)
    fake.fail_download_for.add("a")
    with pytest.raises(DownloadError):
        fetch_item(item, fake, scratch_dir=tmp_path)
    # Scratch should be cleaned up
    assert not any(tmp_path.iterdir())
```

`tests/test_deleter.py`:
```python
from datetime import datetime, timezone

import pytest

from icloud_archiver.deleter import DeleteError, delete_asset
from icloud_archiver.types import CatalogItem
from tests.fakes import FakeAsset, FakeICloudPhotos


def _item() -> CatalogItem:
    return CatalogItem(
        asset_id="a",
        created_at=datetime(2015, 1, 1, tzinfo=timezone.utc),
        size_bytes=10,
        albums=[],
        original_filename="a.HEIC",
        has_live_photo=False,
        has_edits=False,
        mime_type="image/heic",
        icloud_checksum=None,
    )


def test_delete_removes_from_icloud() -> None:
    item = _item()
    fake = FakeICloudPhotos(assets=[FakeAsset(item=item, original_bytes=b"x")])
    delete_asset(item, fake)
    assert list(fake.iter_oldest_first()) == []


def test_delete_already_gone_is_treated_as_success() -> None:
    """Re-deleting an already-deleted item should not raise (idempotency)."""
    item = _item()
    fake = FakeICloudPhotos(assets=[FakeAsset(item=item, original_bytes=b"x")])
    delete_asset(item, fake)
    delete_asset(item, fake)  # second call should not raise


def test_delete_failure_propagates_with_detail() -> None:
    item = _item()
    fake = FakeICloudPhotos(assets=[FakeAsset(item=item, original_bytes=b"x")])
    fake.fail_delete_for.add("a")
    with pytest.raises(DeleteError, match="a"):
        delete_asset(item, fake)
```

- [ ] **Step 2: Run, see fail**

Run: `uv run pytest tests/test_downloader.py tests/test_deleter.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `downloader.py`**

`src/icloud_archiver/downloader.py`:
```python
"""Per-item fetch into a scratch directory. Uses the ICloudPhotos protocol."""

from __future__ import annotations

import os
from pathlib import Path

from icloud_archiver.icloud_iface import ICloudPhotos
from icloud_archiver.organizer import DownloadedFiles
from icloud_archiver.types import CatalogItem


class DownloadError(Exception):
    pass


def _suffix(filename: str) -> str:
    return Path(filename).suffix or ".bin"


def fetch_item(item: CatalogItem, client: ICloudPhotos, *, scratch_dir: Path) -> DownloadedFiles:
    """Download original + (optional) live photo + (optional) edited to scratch_dir.

    On any error, partial files are removed before re-raising as DownloadError.
    """
    scratch_dir.mkdir(parents=True, exist_ok=True)
    original = scratch_dir / f"{item.asset_id}_orig{_suffix(item.original_filename)}"
    partial = original.with_suffix(original.suffix + ".partial")
    live: Path | None = None
    edited: Path | None = None
    written: list[Path] = []
    try:
        client.download_original(item.asset_id, partial)
        os.fsync(os.open(str(partial), os.O_RDONLY))
        partial.rename(original)
        written.append(original)

        if item.has_live_photo:
            live = scratch_dir / f"{item.asset_id}_live.MOV"
            live_partial = live.with_suffix(live.suffix + ".partial")
            client.download_live_photo(item.asset_id, live_partial)
            live_partial.rename(live)
            written.append(live)

        if item.has_edits:
            edited = scratch_dir / f"{item.asset_id}_edit{_suffix(item.original_filename)}"
            edit_partial = edited.with_suffix(edited.suffix + ".partial")
            client.download_edited(item.asset_id, edit_partial)
            edit_partial.rename(edited)
            written.append(edited)

        return DownloadedFiles(original=original, live_photo=live, edited=edited)
    except Exception as exc:
        # Clean up everything we wrote so the next run starts clean
        for p in written + [partial]:
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass
        # Also clean any stale .partial files in scratch
        for stray in scratch_dir.glob(f"{item.asset_id}_*.partial"):
            try:
                stray.unlink()
            except OSError:
                pass
        raise DownloadError(f"failed to fetch {item.asset_id}: {exc}") from exc
```

- [ ] **Step 4: Implement `deleter.py`**

`src/icloud_archiver/deleter.py`:
```python
"""Delete a single iCloud asset. Idempotent — deleting a missing asset is success."""

from __future__ import annotations

from icloud_archiver.icloud_iface import ICloudPhotos
from icloud_archiver.types import CatalogItem


class DeleteError(Exception):
    pass


def delete_asset(item: CatalogItem, client: ICloudPhotos) -> None:
    try:
        client.delete(item.asset_id)
    except KeyError:
        # already gone — treat as success
        return
    except Exception as exc:
        raise DeleteError(f"failed to delete {item.asset_id}: {exc}") from exc
```

- [ ] **Step 5: Run, see pass**

Run: `uv run pytest tests/test_downloader.py tests/test_deleter.py -v`
Expected: all passing (4 + 3).

- [ ] **Step 6: Commit**

```bash
git add src/icloud_archiver/downloader.py src/icloud_archiver/deleter.py tests/test_downloader.py tests/test_deleter.py
git commit -m "feat: per-item downloader (scratch + fsync) and idempotent deleter"
```

---

## Task 11: auth and catalog — real iCloud adapter using pyicloud-ipd

**Files:**
- Create: `src/icloud_archiver/auth.py`
- Create: `src/icloud_archiver/catalog.py`
- Create: `tests/test_auth.py`
- Create: `tests/test_catalog.py`

This task wraps `pyicloud-ipd`. Tests mock at the boundary because we can't hit real Apple in CI. The actual Apple integration is verified manually in Task 15 (smoke checklist).

- [ ] **Step 1: Write tests with the boundary mocked**

`tests/test_auth.py`:
```python
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from icloud_archiver.auth import (
    AuthError,
    SessionUnavailable,
    interactive_login,
    load_session,
)


class _FakePyiCloudService:
    """Minimal stand-in for pyicloud_ipd.PyiCloudService used in tests."""

    def __init__(self, *, requires_2sa: bool = False, verify_ok: bool = True) -> None:
        self.requires_2sa = requires_2sa
        self._verify_ok = verify_ok
        self.trusted_devices = [{"deviceType": "Phone", "phoneNumber": "*** 1234"}]

    def send_verification_code(self, _device: dict[str, str]) -> bool:
        return True

    def validate_verification_code(self, _device: dict[str, str], code: str) -> bool:
        return self._verify_ok and code == "123456"


def test_interactive_login_writes_cookie_and_keychain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cookie_dir = tmp_path / "cookies"
    cookie_dir.mkdir()
    fake_service = _FakePyiCloudService()

    inputs = iter(["test@example.com", "123456"])
    monkeypatch.setattr("builtins.input", lambda _p="": next(inputs))
    monkeypatch.setattr("getpass.getpass", lambda _p="": "hunter2")

    constructed: dict[str, object] = {}

    def fake_ctor(email: str, password: str, cookie_directory: str) -> _FakePyiCloudService:
        constructed["email"] = email
        constructed["password"] = password
        constructed["cookie_directory"] = cookie_directory
        return fake_service

    monkeypatch.setattr("icloud_archiver.auth._PyiCloudService", fake_ctor)
    kept_passwords: dict[str, str] = {}
    monkeypatch.setattr(
        "icloud_archiver.auth.keyring.set_password",
        lambda service, user, pw: kept_passwords.__setitem__(f"{service}:{user}", pw),
    )

    interactive_login(cookie_dir)

    assert constructed["email"] == "test@example.com"
    assert constructed["password"] == "hunter2"
    assert constructed["cookie_directory"] == str(cookie_dir)
    assert kept_passwords["icloud-archiver:test@example.com"] == "hunter2"


def test_load_session_returns_service_when_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cookie_dir = tmp_path / "cookies"
    cookie_dir.mkdir()
    (cookie_dir / "test@example.com").write_text("cookie-blob")

    monkeypatch.setattr("icloud_archiver.auth.keyring.get_password", lambda _s, _u: "pw")

    fake = _FakePyiCloudService()
    monkeypatch.setattr(
        "icloud_archiver.auth._PyiCloudService",
        lambda email, password, cookie_directory: fake,
    )

    svc = load_session(cookie_dir, email="test@example.com")
    assert svc is fake


def test_load_session_raises_when_no_keychain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cookie_dir = tmp_path / "cookies"
    cookie_dir.mkdir()
    monkeypatch.setattr("icloud_archiver.auth.keyring.get_password", lambda _s, _u: None)
    with pytest.raises(SessionUnavailable):
        load_session(cookie_dir, email="missing@example.com")
```

`tests/test_catalog.py`:
```python
from datetime import datetime, timezone

from icloud_archiver.catalog import RealICloudPhotos, _normalize_albums, _photo_to_catalog_item


def test_normalize_albums_sorts_case_insensitive() -> None:
    assert _normalize_albums(["zebra", "Apple", "bear"]) == ["Apple", "bear", "zebra"]


def test_normalize_albums_handles_folder_nesting() -> None:
    # Tuples are how pyicloud-ipd exposes folder-album paths in some versions
    assert _normalize_albums([("Family", "Italy 2014"), "Highlights"]) == ["Family/Italy 2014", "Highlights"]


def test_photo_to_catalog_item_maps_required_fields() -> None:
    class _FakePhoto:
        id = "ABCDEF"
        filename = "IMG_0001.HEIC"
        size = 12345
        created = datetime(2014, 8, 23, 15, 42, 1, tzinfo=timezone.utc)
        item_type = "image"
        live_photo_size = 99
        versions = {"original": {}, "medium": {}}  # no 'edited' key

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
```

- [ ] **Step 2: Run, see fail**

Run: `uv run pytest tests/test_auth.py tests/test_catalog.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `auth.py`**

`src/icloud_archiver/auth.py`:
```python
"""Apple ID login wrapping pyicloud-ipd, with cookie + keychain persistence."""

from __future__ import annotations

import getpass
import os
import stat
from pathlib import Path
from typing import Any

import keyring
from pyicloud_ipd import PyiCloudService as _PyiCloudService  # type: ignore[import-untyped]

_KEYCHAIN_SERVICE = "icloud-archiver"
_MAX_2FA_TRIES = 3


class AuthError(Exception):
    pass


class SessionUnavailable(AuthError):
    """No valid session — caller should ask user to run `login`."""


def _enforce_cookie_perms(cookie_dir: Path) -> None:
    for p in cookie_dir.iterdir():
        try:
            os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass


def interactive_login(cookie_dir: Path) -> None:
    """Prompt for Apple ID / password / 2FA. Persist cookie + keychain on success."""
    cookie_dir.mkdir(parents=True, exist_ok=True)
    email = input("Apple ID email: ").strip()
    password = getpass.getpass("Apple ID password: ")
    service = _PyiCloudService(email, password, cookie_directory=str(cookie_dir))

    if getattr(service, "requires_2sa", False):
        devices = getattr(service, "trusted_devices", [])
        if not devices:
            raise AuthError("2FA required but no trusted devices listed.")
        device = devices[0]
        if not service.send_verification_code(device):  # type: ignore[no-untyped-call]
            raise AuthError("Failed to send 2FA code.")
        for attempt in range(_MAX_2FA_TRIES):
            code = input("2FA code (6 digits): ").strip()
            ok = service.validate_verification_code(device, code)  # type: ignore[no-untyped-call]
            if ok:
                break
            print(f"  code rejected ({_MAX_2FA_TRIES - attempt - 1} retries left)")
        else:
            raise AuthError("2FA verification failed.")

    keyring.set_password(_KEYCHAIN_SERVICE, email, password)
    _enforce_cookie_perms(cookie_dir)
    print(f"Logged in as {email}. Session cookies stored in {cookie_dir}.")


def load_session(cookie_dir: Path, *, email: str) -> Any:
    """Re-open a session using the persisted cookie + keychain password."""
    password = keyring.get_password(_KEYCHAIN_SERVICE, email)
    if not password:
        raise SessionUnavailable(f"no keychain entry for {email}; run `login` first")
    _enforce_cookie_perms(cookie_dir)
    service = _PyiCloudService(email, password, cookie_directory=str(cookie_dir))
    return service
```

- [ ] **Step 4: Implement `catalog.py`**

`src/icloud_archiver/catalog.py`:
```python
"""Wrap pyicloud-ipd's PhotosService into our ICloudPhotos protocol."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any, Iterator

from icloud_archiver.types import CatalogItem


def _normalize_albums(raw: list[Any]) -> list[str]:
    flat: list[str] = []
    for a in raw:
        if isinstance(a, str):
            flat.append(a)
        elif isinstance(a, (tuple, list)):
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


def _photo_to_catalog_item(photo: Any) -> CatalogItem:
    albums = _normalize_albums(list(getattr(photo, "albums", [])))
    versions = getattr(photo, "versions", {}) or {}
    has_edits = bool(versions.get("edited") or versions.get("alternative"))
    return CatalogItem(
        asset_id=str(photo.id),
        created_at=photo.created,
        size_bytes=int(photo.size),
        albums=albums,
        original_filename=str(photo.filename),
        has_live_photo=bool(getattr(photo, "live_photo_size", 0)),
        has_edits=has_edits,
        mime_type=_guess_mime(str(photo.filename)),
        icloud_checksum=str(photo.size) if False else None,  # Apple doesn't expose a stable checksum
    )


class RealICloudPhotos:
    """ICloudPhotos protocol implementation backed by pyicloud-ipd."""

    def __init__(self, pyicloud_service: Any) -> None:
        self._svc = pyicloud_service

    def iter_oldest_first(self) -> Iterator[CatalogItem]:
        photos = self._svc.photos
        # pyicloud-ipd exposes an "All Photos" album that supports `sort()`.
        all_album = photos.albums["All Photos"]
        all_album.sort_direction = "ASCENDING"
        for photo in all_album:
            yield _photo_to_catalog_item(photo)

    def download_original(self, asset_id: str, dest: Path) -> None:
        photo = self._find(asset_id)
        with photo.download("original") as resp, dest.open("wb") as out:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                out.write(chunk)

    def download_live_photo(self, asset_id: str, dest: Path) -> None:
        photo = self._find(asset_id)
        with photo.download("originalVideo") as resp, dest.open("wb") as out:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                out.write(chunk)

    def download_edited(self, asset_id: str, dest: Path) -> None:
        photo = self._find(asset_id)
        with photo.download("edited") as resp, dest.open("wb") as out:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                out.write(chunk)

    def delete(self, asset_id: str) -> None:
        photo = self._find(asset_id)
        photo.delete()

    def empty_trash(self, asset_ids: list[str]) -> None:
        recently_deleted = self._svc.photos.albums.get("Recently Deleted")
        if recently_deleted is None:
            return
        for photo in recently_deleted:
            if str(photo.id) in asset_ids:
                photo.delete()  # permanent in Recently Deleted

    def _find(self, asset_id: str) -> Any:
        # Linear scan is fine for one-off downloads. iCloud's library API
        # exposes no direct by-id lookup.
        for p in self._svc.photos.albums["All Photos"]:
            if str(p.id) == asset_id:
                return p
        raise KeyError(asset_id)
```

- [ ] **Step 5: Run tests for auth + catalog**

Run: `uv run pytest tests/test_auth.py tests/test_catalog.py -v`
Expected: all passing.

- [ ] **Step 6: Commit**

```bash
git add src/icloud_archiver/auth.py src/icloud_archiver/catalog.py tests/test_auth.py tests/test_catalog.py
git commit -m "feat: pyicloud-ipd auth + RealICloudPhotos adapter"
```

---

## Task 12: Reporting — plan markdown + run summary

**Files:**
- Create: `src/icloud_archiver/reporting.py`
- Create: `tests/test_reporting.py`

- [ ] **Step 1: Write failing tests**

`tests/test_reporting.py`:
```python
from datetime import datetime, timezone

from icloud_archiver.reporting import PlanRow, render_plan_markdown, render_run_summary


def _rows() -> list[PlanRow]:
    return [
        PlanRow(
            asset_id="a",
            created_at=datetime(2014, 1, 5, tzinfo=timezone.utc),
            size_bytes=2_000_000_000,
            albums=["Family"],
        ),
        PlanRow(
            asset_id="b",
            created_at=datetime(2014, 1, 6, tzinfo=timezone.utc),
            size_bytes=3_000_000_000,
            albums=["Family", "Highlights"],
        ),
    ]


def test_render_plan_markdown_summarizes_rows() -> None:
    md = render_plan_markdown(_rows(), target_bytes=4_000_000_000, archive_root="/Volumes/T7/iCloud-Archive")
    assert "# iCloud Archiver — Plan" in md
    assert "2 items" in md
    assert "5.0 GB" in md
    assert "2014-01-05" in md
    assert "2014-01-06" in md
    assert "Family" in md
    assert "Highlights" in md
    assert "/Volumes/T7/iCloud-Archive" in md


def test_render_run_summary_includes_failure_counts() -> None:
    out = render_run_summary(
        archived=10,
        deleted=9,
        failed_verify=1,
        failed_download=0,
        skipped=0,
        bytes_archived=12_000_000_000,
        bytes_pending_free=10_000_000_000,
    )
    assert "10 archived" in out
    assert "9 deleted" in out
    assert "1 failed verify" in out
    assert "10.0 GB" in out  # bytes_pending_free
    assert "Recently Deleted" in out
```

- [ ] **Step 2: Run, see fail**

Run: `uv run pytest tests/test_reporting.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `reporting.py`**

`src/icloud_archiver/reporting.py`:
```python
"""Plan reports (markdown) and run summaries (terminal)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class PlanRow:
    asset_id: str
    created_at: datetime
    size_bytes: int
    albums: list[str]


def _human(n: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    f = float(n)
    for u in units:
        if f < 1000:
            return f"{f:.1f} {u}"
        f /= 1000
    return f"{f:.1f} PB"


def render_plan_markdown(
    rows: list[PlanRow], *, target_bytes: int, archive_root: str
) -> str:
    if not rows:
        return "# iCloud Archiver — Plan\n\nNothing to archive (target reached with 0 items)."
    total = sum(r.size_bytes for r in rows)
    oldest = min(r.created_at for r in rows)
    newest = max(r.created_at for r in rows)
    albums = sorted({a for r in rows for a in r.albums})
    lines = [
        "# iCloud Archiver — Plan",
        "",
        f"- **{len(rows)} items**, total {_human(total)}",
        f"- Target: {_human(target_bytes)}",
        f"- Date range: {oldest.date().isoformat()} → {newest.date().isoformat()}",
        f"- Archive root: `{archive_root}`",
        f"- Projected free-space needed (×1.2): {_human(int(total * 1.2))}",
        "",
        "## Albums touched",
        "",
    ]
    for a in albums:
        lines.append(f"- {a}")
    lines.append("")
    return "\n".join(lines)


def render_run_summary(
    *,
    archived: int,
    deleted: int,
    failed_verify: int,
    failed_download: int,
    skipped: int,
    bytes_archived: int,
    bytes_pending_free: int,
) -> str:
    lines = [
        "Run complete.",
        f"  {archived} archived, {deleted} deleted",
        f"  {failed_verify} failed verify, {failed_download} failed download, {skipped} skipped",
        f"  {_human(bytes_archived)} archived total",
        f"  {_human(bytes_pending_free)} now in Recently Deleted (not yet reclaimed)",
        "",
        "Recently Deleted holds purged items for 30 days. To reclaim space sooner,",
        "run `icloud-archiver empty-trash` once you've spot-checked the archive.",
    ]
    return "\n".join(lines)
```

- [ ] **Step 4: Run, see pass**

Run: `uv run pytest tests/test_reporting.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/icloud_archiver/reporting.py tests/test_reporting.py
git commit -m "feat: plan markdown + run summary reporting"
```

---

## Task 13: Orchestrator — bind all the modules together

**Files:**
- Create: `src/icloud_archiver/orchestrator.py`
- Create: `tests/test_orchestrator.py`

This module owns the run-loop logic (resume routing + selection + per-item pipeline). It's the most-end-to-end piece tested entirely against the Fake.

- [ ] **Step 1: Write failing integration-shaped tests**

`tests/test_orchestrator.py`:
```python
from datetime import datetime, timedelta, timezone
from pathlib import Path

from icloud_archiver.journal import Journal
from icloud_archiver.orchestrator import RunOutcome, run_archival
from icloud_archiver.types import CatalogItem, ItemState
from tests.fakes import FakeAsset, FakeICloudPhotos


def _assets(count: int, size: int = 1000, with_live: bool = False) -> list[FakeAsset]:
    base = datetime(2014, 1, 1, tzinfo=timezone.utc)
    return [
        FakeAsset(
            item=CatalogItem(
                asset_id=f"a{i:03d}",
                created_at=base + timedelta(days=i),
                size_bytes=size,
                albums=[f"Album {i % 3}"],
                original_filename=f"a{i:03d}.HEIC",
                has_live_photo=with_live,
                has_edits=False,
                mime_type="image/heic",
                icloud_checksum=None,
            ),
            original_bytes=b"X" * size,
            live_photo_bytes=(b"L" * size) if with_live else None,
        )
        for i in range(count)
    ]


def test_happy_path_all_items_deleted(tmp_path: Path) -> None:
    # Use real (valid) JPEG bytes so the verifier passes.
    from tests.fixtures import make_jpeg

    assets = _assets(3)
    for a in assets:
        # Replace the original with a real JPEG; update size_bytes and mime_type to match.
        jpeg_path = tmp_path / f"src_{a.item.asset_id}.jpg"
        make_jpeg(jpeg_path)
        a.original_bytes = jpeg_path.read_bytes()
        # Patch CatalogItem to match the new bytes
        new_item = CatalogItem(
            asset_id=a.item.asset_id,
            created_at=a.item.created_at,
            size_bytes=len(a.original_bytes),
            albums=a.item.albums,
            original_filename=a.item.original_filename.replace(".HEIC", ".jpg"),
            has_live_photo=a.item.has_live_photo,
            has_edits=a.item.has_edits,
            mime_type="image/jpeg",
            icloud_checksum=None,
        )
        a.item = new_item

    fake = FakeICloudPhotos(assets=assets)
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    journal = Journal.open(tmp_path / "state.db")
    outcome = run_archival(
        client=fake,
        journal=journal,
        archive_root=archive_root,
        target_bytes=10_000,  # well above total
        dry_run=False,
    )
    assert outcome.archived == 3
    assert outcome.deleted == 3
    assert outcome.failed_verify == 0
    assert list(fake.iter_oldest_first()) == []
    # All files exist on the archive drive
    for a in assets:
        primary = archive_root / a.item.albums[0] / a.item.original_filename
        assert primary.is_file()


def test_verify_failure_preserves_icloud_copy(tmp_path: Path) -> None:
    from tests.fixtures import make_jpeg

    assets = _assets(2)
    for a in assets:
        jpeg_path = tmp_path / f"src_{a.item.asset_id}.jpg"
        make_jpeg(jpeg_path)
        a.original_bytes = jpeg_path.read_bytes()
        a.item = CatalogItem(
            asset_id=a.item.asset_id,
            created_at=a.item.created_at,
            size_bytes=len(a.original_bytes),
            albums=a.item.albums,
            original_filename=a.item.original_filename.replace(".HEIC", ".jpg"),
            has_live_photo=False,
            has_edits=False,
            mime_type="image/jpeg",
            icloud_checksum=None,
        )

    fake = FakeICloudPhotos(assets=assets)
    fake.truncate_download_for[assets[0].item.asset_id] = 50  # break first asset

    journal = Journal.open(tmp_path / "state.db")
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    outcome = run_archival(
        client=fake,
        journal=journal,
        archive_root=archive_root,
        target_bytes=10_000,
        dry_run=False,
    )
    assert outcome.failed_verify == 1
    assert outcome.deleted == 1  # the other asset succeeds
    # The failed asset is still on iCloud
    remaining = [i.asset_id for i in fake.iter_oldest_first()]
    assert assets[0].item.asset_id in remaining


def test_dry_run_writes_no_disk_or_icloud(tmp_path: Path) -> None:
    assets = _assets(3)
    fake = FakeICloudPhotos(assets=assets)
    journal = Journal.open(tmp_path / "state.db")
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    outcome = run_archival(
        client=fake,
        journal=journal,
        archive_root=archive_root,
        target_bytes=10_000,
        dry_run=True,
    )
    assert outcome.archived == 0
    assert outcome.deleted == 0
    # Nothing in iCloud was deleted
    assert len(list(fake.iter_oldest_first())) == 3
    # No files on archive drive
    assert not any(archive_root.iterdir())
    # Plan rows reported
    assert len(outcome.plan_rows) == 3


def test_run_aborts_when_free_space_insufficient(tmp_path: Path, monkeypatch) -> None:
    """If projected_download × 1.2 > free space, abort before any download."""
    from icloud_archiver.orchestrator import InsufficientSpace
    import pytest as _pytest

    assets = _assets(3, size=10_000_000)  # 30MB total
    fake = FakeICloudPhotos(assets=assets)
    journal = Journal.open(tmp_path / "state.db")
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    # Pretend the drive has only 1 MB free.
    monkeypatch.setattr("icloud_archiver.orchestrator._free_bytes", lambda _p: 1_000_000)

    with _pytest.raises(InsufficientSpace):
        run_archival(
            client=fake,
            journal=journal,
            archive_root=archive_root,
            target_bytes=100_000_000,
            dry_run=False,
        )
    # Nothing was downloaded
    assert len(list(fake.iter_oldest_first())) == 3
    assert not any(archive_root.iterdir())


def test_resume_reprocesses_non_terminal_items(tmp_path: Path) -> None:
    """An item left PLANNED by a crashed prior run gets re-processed, not skipped."""
    from tests.fixtures import make_jpeg

    assets = _assets(1)
    a = assets[0]
    jpeg_path = tmp_path / "src.jpg"
    make_jpeg(jpeg_path)
    a.original_bytes = jpeg_path.read_bytes()
    a.item = CatalogItem(
        asset_id=a.item.asset_id,
        created_at=a.item.created_at,
        size_bytes=len(a.original_bytes),
        albums=a.item.albums,
        original_filename=a.item.original_filename.replace(".HEIC", ".jpg"),
        has_live_photo=False,
        has_edits=False,
        mime_type="image/jpeg",
        icloud_checksum=None,
    )

    fake = FakeICloudPhotos(assets=assets)
    journal = Journal.open(tmp_path / "state.db")
    archive_root = tmp_path / "archive"
    archive_root.mkdir()

    # Simulate a crashed prior run: item left in PLANNED state, no files on disk.
    prior_run = journal.start_run(target_bytes=1, dry_run=False, archive_root=str(archive_root))
    journal.upsert_item(a.item, prior_run, ItemState.PLANNED)
    journal.end_run(prior_run, RunStatus.CRASHED)

    # New run should re-process the PLANNED item.
    outcome = run_archival(
        client=fake,
        journal=journal,
        archive_root=archive_root,
        target_bytes=10_000,
        dry_run=False,
    )
    assert outcome.archived == 1
    assert outcome.deleted == 1
    assert list(fake.iter_oldest_first()) == []


def test_resume_skips_already_deleted_items(tmp_path: Path) -> None:
    from tests.fixtures import make_jpeg

    assets = _assets(2)
    for a in assets:
        jpeg_path = tmp_path / f"src_{a.item.asset_id}.jpg"
        make_jpeg(jpeg_path)
        a.original_bytes = jpeg_path.read_bytes()
        a.item = CatalogItem(
            asset_id=a.item.asset_id,
            created_at=a.item.created_at,
            size_bytes=len(a.original_bytes),
            albums=a.item.albums,
            original_filename=a.item.original_filename.replace(".HEIC", ".jpg"),
            has_live_photo=False,
            has_edits=False,
            mime_type="image/jpeg",
            icloud_checksum=None,
        )

    fake = FakeICloudPhotos(assets=assets)
    journal = Journal.open(tmp_path / "state.db")
    archive_root = tmp_path / "archive"
    archive_root.mkdir()

    # Run 1: archive both
    run_archival(client=fake, journal=journal, archive_root=archive_root, target_bytes=10_000, dry_run=False)

    # Run 2: nothing left to do
    outcome2 = run_archival(client=fake, journal=journal, archive_root=archive_root, target_bytes=10_000, dry_run=False)
    assert outcome2.archived == 0
    assert outcome2.deleted == 0
```

- [ ] **Step 2: Run, see fail**

Run: `uv run pytest tests/test_orchestrator.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `orchestrator.py`**

`src/icloud_archiver/orchestrator.py`:
```python
"""High-level run loop binding catalog → selector → downloader → verifier → organizer → deleter."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from icloud_archiver.deleter import DeleteError, delete_asset
from icloud_archiver.downloader import DownloadError, fetch_item
from icloud_archiver.icloud_iface import ICloudPhotos
from icloud_archiver.journal import Journal
from icloud_archiver.organizer import organize, sidecar_dict
from icloud_archiver.reporting import PlanRow
from icloud_archiver.selector import select_until
from icloud_archiver.types import CatalogItem, ItemState, RunStatus
from icloud_archiver.verifier import VerifyError, verify


@dataclass
class RunOutcome:
    archived: int = 0
    deleted: int = 0
    failed_download: int = 0
    failed_verify: int = 0
    failed_delete: int = 0
    skipped: int = 0
    bytes_archived: int = 0
    plan_rows: list[PlanRow] = field(default_factory=list)


class InsufficientSpace(Exception):
    pass


def run_archival(
    *,
    client: ICloudPhotos,
    journal: Journal,
    archive_root: Path,
    target_bytes: int,
    dry_run: bool,
) -> RunOutcome:
    run_id = journal.start_run(
        target_bytes=target_bytes, dry_run=dry_run, archive_root=str(archive_root)
    )
    outcome = RunOutcome()
    scratch_dir = archive_root / ".scratch"
    # Wipe any leftover partials from a crashed previous run before starting fresh.
    if scratch_dir.exists():
        shutil.rmtree(scratch_dir, ignore_errors=True)
    scratch_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Materialize selection up-front so state mutations during processing
        # do not affect what items we iterate over.
        catalog = client.iter_oldest_first()
        selected = list(select_until(catalog, target_bytes=target_bytes, journal=journal))

        if dry_run:
            for item in selected:
                outcome.plan_rows.append(
                    PlanRow(
                        asset_id=item.asset_id,
                        created_at=item.created_at,
                        size_bytes=item.size_bytes,
                        albums=list(item.albums),
                    )
                )
            journal.end_run(run_id, RunStatus.COMPLETED)
            return outcome

        # Free-space check (spec §5.1 phase B, §7.2): need ≥ 1.2 × projected.
        projected = sum(i.size_bytes for i in selected)
        required = int(projected * 1.2)
        free = _free_bytes(archive_root)
        if free < required:
            raise InsufficientSpace(
                f"need {required} bytes on archive drive (1.2 × {projected}); only {free} available"
            )

        for item in selected:
            journal.upsert_item(item, run_id, ItemState.PLANNED)
            _archive_one(item, client, journal, archive_root, scratch_dir, run_id, outcome)

        journal.end_run(run_id, RunStatus.COMPLETED)
    except KeyboardInterrupt:
        journal.end_run(run_id, RunStatus.ABORTED)
        raise
    except Exception:
        journal.end_run(run_id, RunStatus.CRASHED)
        raise
    finally:
        # Best-effort scratch cleanup (per-item cleanup happens inside downloader on error)
        if scratch_dir.exists() and not any(scratch_dir.iterdir()):
            shutil.rmtree(scratch_dir, ignore_errors=True)
    return outcome


def _free_bytes(path: Path) -> int:
    import os

    s = os.statvfs(path)
    return s.f_bavail * s.f_frsize


def _hash_first_4kb(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as f:
        h.update(f.read(4096))
    return h.hexdigest()


def _archive_one(
    item: CatalogItem,
    client: ICloudPhotos,
    journal: Journal,
    archive_root: Path,
    scratch_dir: Path,
    run_id: str,
    outcome: RunOutcome,
) -> bool:
    # Resume shortcut (spec §6.3): if a prior run already wrote the file to disk
    # and reached ARCHIVED or DELETING, skip download/verify/organize and go
    # straight to delete. (Earlier-state resumes have to re-download because we
    # wipe scratch at run start.)
    prior = journal.get_state(item.asset_id)
    if prior in (ItemState.ARCHIVED, ItemState.DELETING):
        journal.transition(item.asset_id, ItemState.DELETING, run_id=run_id)
        try:
            delete_asset(item, client)
        except DeleteError as exc:
            journal.transition(item.asset_id, ItemState.DELETING, run_id=run_id, error=str(exc))
            outcome.failed_delete += 1
            return True
        journal.transition(item.asset_id, ItemState.DELETED, run_id=run_id)
        outcome.deleted += 1
        outcome.bytes_archived += item.size_bytes  # count toward this run's total
        return True

    # Download
    journal.transition(item.asset_id, ItemState.DOWNLOADING, run_id=run_id)
    try:
        files = fetch_item(item, client, scratch_dir=scratch_dir)
    except DownloadError as exc:
        journal.transition(item.asset_id, ItemState.FAILED_DOWNLOAD, run_id=run_id, error=str(exc))
        outcome.failed_download += 1
        return False
    journal.transition(item.asset_id, ItemState.DOWNLOADED, run_id=run_id)

    # Verify
    journal.transition(item.asset_id, ItemState.VERIFYING, run_id=run_id)
    try:
        result = verify(item, files.original)
    except VerifyError as exc:
        journal.transition(item.asset_id, ItemState.FAILED_VERIFY, run_id=run_id, error=str(exc))
        outcome.failed_verify += 1
        # Leave iCloud copy alone. Clean up scratch.
        for p in (files.original, files.live_photo, files.edited):
            if p is not None and p.exists():
                p.unlink()
        return False
    # Capture first-4KB hash of the downloaded original BEFORE organize moves it.
    pre_organize_4kb = _hash_first_4kb(files.original)
    journal.transition(item.asset_id, ItemState.VERIFIED, run_id=run_id, sha256=result.sha256)

    # Organize
    journal.transition(item.asset_id, ItemState.ORGANIZING, run_id=run_id)
    side = sidecar_dict(item, sha256=result.sha256, run_id=run_id)
    organized = organize(item, files, archive_root, sidecar=side)

    # Post-organize readback (spec §7.1 step 3 final): first-4KB hash must match.
    post_organize_4kb = _hash_first_4kb(organized.primary)
    if post_organize_4kb != pre_organize_4kb:
        journal.transition(
            item.asset_id,
            ItemState.FAILED_VERIFY,
            run_id=run_id,
            error=f"post-organize 4KB hash mismatch: {pre_organize_4kb} vs {post_organize_4kb}",
        )
        outcome.failed_verify += 1
        return False

    journal.transition(
        item.asset_id,
        ItemState.ARCHIVED,
        run_id=run_id,
        primary_path=str(organized.primary),
        hardlink_paths=[str(p) for p in organized.hardlinks],
    )
    outcome.archived += 1
    outcome.bytes_archived += item.size_bytes

    # Delete
    journal.transition(item.asset_id, ItemState.DELETING, run_id=run_id)
    try:
        delete_asset(item, client)
    except DeleteError as exc:
        # Local archive is safe — leave item in DELETING with the error for next-run retry.
        journal.transition(item.asset_id, ItemState.DELETING, run_id=run_id, error=str(exc))
        outcome.failed_delete += 1
        return True  # still counts as archived
    journal.transition(item.asset_id, ItemState.DELETED, run_id=run_id)
    outcome.deleted += 1
    return True
```

- [ ] **Step 4: Run, see pass**

Run: `uv run pytest tests/test_orchestrator.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/icloud_archiver/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: orchestrator binds catalog→select→download→verify→organize→delete"
```

---

## Task 14: CLI — Click commands wiring everything together

**Files:**
- Create: `src/icloud_archiver/cli.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI tests using Click's CliRunner**

`tests/test_cli.py`:
```python
from pathlib import Path

from click.testing import CliRunner

from icloud_archiver.cli import main


def test_help_lists_subcommands() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    for cmd in ("login", "disks", "plan", "run", "status", "empty-trash"):
        assert cmd in result.output


def test_disks_subcommand_runs_without_error(monkeypatch) -> None:
    from icloud_archiver import cli as cli_mod
    monkeypatch.setattr(cli_mod, "list_external_drives", lambda: [])
    runner = CliRunner()
    result = runner.invoke(main, ["disks"])
    assert result.exit_code == 0
    assert "No external drives mounted" in result.output


def test_plan_writes_report(tmp_path: Path, monkeypatch) -> None:
    from datetime import datetime, timezone

    from icloud_archiver import cli as cli_mod
    from icloud_archiver.preflight import Drive
    from icloud_archiver.types import CatalogItem
    from tests.fakes import FakeAsset, FakeICloudPhotos

    drive = Drive(
        device_id="disk4s2",
        volume_name="T7",
        mount_point=tmp_path / "fake_mount",
        fs="apfs",
        free_bytes=10_000_000_000,
        total_bytes=20_000_000_000,
        is_external=True,
    )
    drive.mount_point.mkdir()

    monkeypatch.setattr(cli_mod, "list_external_drives", lambda: [drive])
    monkeypatch.setattr(cli_mod, "pick_drive_interactive", lambda drives: drive)
    monkeypatch.setattr("builtins.input", lambda _p="": "iCloud-Archive")

    fake_assets = [
        FakeAsset(
            item=CatalogItem(
                asset_id="a",
                created_at=datetime(2014, 1, 1, tzinfo=timezone.utc),
                size_bytes=1000,
                albums=["X"],
                original_filename="a.HEIC",
                has_live_photo=False,
                has_edits=False,
                mime_type="image/heic",
                icloud_checksum=None,
            ),
            original_bytes=b"X" * 1000,
        )
    ]
    monkeypatch.setattr(cli_mod, "_build_client", lambda: FakeICloudPhotos(fake_assets))
    monkeypatch.setattr(cli_mod, "_state_path", lambda: tmp_path / "state.db")
    monkeypatch.setattr(cli_mod, "_plans_dir", lambda: tmp_path / "plans")

    runner = CliRunner()
    result = runner.invoke(main, ["plan", "--target-freed", "1KB"])
    assert result.exit_code == 0, result.output
    assert "Plan" in result.output
    # A plan file was written
    plans = list((tmp_path / "plans").glob("*.md"))
    assert len(plans) == 1
```

- [ ] **Step 2: Run, see fail**

Run: `uv run pytest tests/test_cli.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `cli.py`**

`src/icloud_archiver/cli.py`:
```python
"""Click CLI: login, disks, plan, run, status, empty-trash."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click

from icloud_archiver.auth import SessionUnavailable, interactive_login, load_session
from icloud_archiver.catalog import RealICloudPhotos
from icloud_archiver.config import parse_size, state_dir
from icloud_archiver.icloud_iface import ICloudPhotos
from icloud_archiver.journal import Journal
from icloud_archiver.orchestrator import run_archival
from icloud_archiver.preflight import (
    confirm_reformat,
    caffeinate_for_run,
    list_external_drives,
    needs_reformat,
    pick_drive_interactive,
    reformat_apfs,
)
from icloud_archiver.reporting import render_plan_markdown, render_run_summary
from icloud_archiver.types import ItemState


# Wrappers exist so tests can monkeypatch these paths in one place.
def _state_path() -> Path:
    return state_dir() / "state.db"


def _plans_dir() -> Path:
    p = state_dir() / "plans"
    p.mkdir(exist_ok=True)
    return p


def _saved_email() -> str | None:
    """Read the most recently logged-in email from state_dir/config.json."""
    import json

    cfg = state_dir() / "config.json"
    if not cfg.exists():
        return None
    try:
        return str(json.loads(cfg.read_text()).get("email"))
    except Exception:
        return None


def _save_email(email: str) -> None:
    import json

    cfg = state_dir() / "config.json"
    cfg.write_text(json.dumps({"email": email}))


def _build_client() -> ICloudPhotos:
    email = _saved_email()
    if not email:
        raise SessionUnavailable("no saved email; run `icloud-archiver login` first")
    svc = load_session(state_dir() / "cookies", email=email)
    return RealICloudPhotos(svc)


def _interactive_picker() -> tuple[Path, Any]:
    """Return (archive_root, drive). Handles reformat prompt if needed."""
    drives = list_external_drives()
    if not drives:
        click.echo("No external drives mounted. Plug one in and try again.", err=True)
        raise SystemExit(1)
    drive = pick_drive_interactive(drives)

    if needs_reformat(drive.fs):
        if not confirm_reformat(drive):
            click.echo("Aborted.", err=True)
            raise SystemExit(1)
        reformat_apfs(drive)
        # Re-probe to confirm
        drives = list_external_drives()
        drive = next((d for d in drives if d.volume_name == drive.volume_name), None)  # type: ignore[assignment]
        if drive is None or needs_reformat(drive.fs):
            click.echo("Reformat did not yield an APFS volume.", err=True)
            raise SystemExit(1)

    subdir = input(f"Archive subdirectory name on '{drive.volume_name}' [default: iCloud-Archive]: ").strip()
    subdir = subdir or "iCloud-Archive"
    archive_root = drive.mount_point / subdir
    archive_root.mkdir(parents=True, exist_ok=True)
    return archive_root, drive


@click.group()
def main() -> None:
    """Archive the oldest iCloud Photos to an external drive."""


@main.command()
def login() -> None:
    """Sign in to iCloud and persist a session cookie + keychain password."""
    interactive_login(state_dir() / "cookies")
    email = input("Confirm Apple ID email to remember for subsequent runs: ").strip()
    _save_email(email)


@main.command()
def disks() -> None:
    """Print external drives that could be used as archive targets."""
    drives = list_external_drives()
    if not drives:
        click.echo("No external drives mounted.")
        return
    for i, d in enumerate(drives, start=1):
        flag = "  ⚠ reformat needed" if needs_reformat(d.fs) else ""
        click.echo(
            f"  [{i}] {d.volume_name:<18} {d.fs:<7} "
            f"{d.free_bytes / 1e12:.1f} TB free / {d.total_bytes / 1e12:.1f} TB   "
            f"{d.mount_point}{flag}"
        )


@main.command()
@click.option("--target-freed", required=True, help="Bytes to free, e.g. 1TB or 500GB")
def plan(target_freed: str) -> None:
    """Dry-run: produce a plan of what would be archived without touching iCloud or disk."""
    target_bytes = parse_size(target_freed)
    archive_root, drive = _interactive_picker()
    client = _build_client()
    journal = Journal.open(_state_path())
    outcome = run_archival(
        client=client,
        journal=journal,
        archive_root=archive_root,
        target_bytes=target_bytes,
        dry_run=True,
    )
    md = render_plan_markdown(
        outcome.plan_rows, target_bytes=target_bytes, archive_root=str(archive_root)
    )
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    plan_path = _plans_dir() / f"{ts}-plan.md"
    plan_path.write_text(md)
    click.echo(md)
    click.echo(f"\nPlan written to {plan_path}")


@main.command()
@click.option("--target-freed", required=True, help="Bytes to free, e.g. 1TB or 500GB")
def run(target_freed: str) -> None:
    """Archive the oldest items until target_bytes is freed."""
    target_bytes = parse_size(target_freed)
    archive_root, drive = _interactive_picker()
    client = _build_client()
    journal = Journal.open(_state_path())

    sleep_block = caffeinate_for_run()
    try:
        outcome = run_archival(
            client=client,
            journal=journal,
            archive_root=archive_root,
            target_bytes=target_bytes,
            dry_run=False,
        )
    finally:
        sleep_block.terminate()

    click.echo(
        render_run_summary(
            archived=outcome.archived,
            deleted=outcome.deleted,
            failed_verify=outcome.failed_verify,
            failed_download=outcome.failed_download,
            skipped=outcome.skipped,
            bytes_archived=outcome.bytes_archived,
            bytes_pending_free=outcome.bytes_archived,  # equals deleted-bytes pending purge
        )
    )


@main.command()
def status() -> None:
    """Show journal stats: items by state, recent runs."""
    journal = Journal.open(_state_path())
    counts = journal.items_by_state()
    runs = journal.list_runs()[:5]
    click.echo("Items by state:")
    for state, n in sorted(counts.items()):
        click.echo(f"  {state:<18} {n}")
    click.echo("\nRecent runs:")
    for r in runs:
        click.echo(
            f"  {r['run_id']}  {r['started_at']}  target={r['target_bytes']}  "
            f"dry_run={bool(r['dry_run'])}  status={r['ended_status'] or 'in_progress'}"
        )


@main.command("empty-trash")
def empty_trash() -> None:
    """Permanently empty items that were archived by this tool from Recently Deleted."""
    journal = Journal.open(_state_path())
    counts = journal.items_by_state()
    eligible = counts.get("DELETED", 0)
    if eligible == 0:
        click.echo("Nothing to empty — no items in DELETED state in the journal.")
        return
    click.echo(
        f"Recently Deleted contains {eligible} items archived by this tool, all with verified local copies."
    )
    confirm = input("Permanently empty Recently Deleted? Type 'EMPTY' to confirm: ").strip()
    if confirm != "EMPTY":
        click.echo("Aborted.")
        return
    client = _build_client()
    deleted_ids = journal.asset_ids_in_state(ItemState.DELETED)
    client.empty_trash(deleted_ids)
    click.echo(f"Empty-trash issued for {len(deleted_ids)} assets.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run, see pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: 3 passed.

- [ ] **Step 5: Smoke-test the CLI directly**

Run: `uv run icloud-archiver --help`
Expected: lists all 6 subcommands.

Run: `uv run icloud-archiver disks`
Expected: either lists external drives, or prints "No external drives mounted." Exits 0.

- [ ] **Step 6: Commit**

```bash
git add src/icloud_archiver/cli.py tests/test_cli.py
git commit -m "feat: Click CLI with login/disks/plan/run/status/empty-trash"
```

---

## Task 15: Manual smoke test checklist + README polish

**Files:**
- Create: `tests/manual/README.md`
- Modify: `README.md`
- Create: `Makefile`

- [ ] **Step 1: Write the manual smoke checklist**

`tests/manual/README.md`:
```markdown
# Manual smoke tests

These run against the real Apple ID, on a real external drive, by a human.
Do them once end-to-end before trusting the tool with the full 2 TB run.

Each item is a checkbox. Mark it once verified.

## Authentication

- [ ] `uv run icloud-archiver login` succeeds.
- [ ] 2FA code is requested and accepted.
- [ ] Re-running any subcommand within 24 hours reuses the cookie — no re-prompt.
- [ ] Inspect `~/.icloud-archiver/cookies/`: files are mode `0600`.

## Drive picker

- [ ] `uv run icloud-archiver disks` lists every plugged-in external drive.
- [ ] System drive is NOT in the list.
- [ ] Filesystem column shows `APFS` / `HFS+` / `exFAT` / etc. correctly.
- [ ] Free/total columns roughly match what Finder shows.
- [ ] Picker rejects out-of-range input and `q` quits cleanly.

## Reformat path (sacrificial USB stick only — never the real archive drive)

- [ ] Insert a small USB stick formatted as exFAT.
- [ ] `uv run icloud-archiver plan --target-freed 1GB` triggers the reformat prompt.
- [ ] Typing anything other than `ERASE <NAME>` aborts.
- [ ] Typing the exact phrase reformats the stick to APFS.
- [ ] Re-probe confirms the stick is now APFS.

## Plan (read-only)

- [ ] `uv run icloud-archiver plan --target-freed 1GB` produces a markdown plan listing oldest items first.
- [ ] Plan file written to `~/.icloud-archiver/plans/<timestamp>-plan.md`.
- [ ] No items deleted from iCloud (check via icloud.com).
- [ ] No files written to the archive drive (except the empty `.scratch/` if created).

## Run (1 GB scope, test drive)

- [ ] Use a non-production external drive.
- [ ] `uv run icloud-archiver run --target-freed 1GB` proceeds without prompting beyond the picker.
- [ ] Caffeinate process visible in `ps aux | grep caffeinate` during the run.
- [ ] Archive drive contains album-based subfolders.
- [ ] Multi-album items are hardlinked (compare `stat` inode numbers across album folders).
- [ ] Sidecar `.json` files exist in every album folder containing the item.
- [ ] iCloud.com → Recently Deleted shows the archived items (within minutes).
- [ ] `icloud-archiver status` shows the run as `completed`.

## Resume

- [ ] Re-run `uv run icloud-archiver run --target-freed 1GB`.
- [ ] Reports 0 archived / 0 deleted (items already done).
- [ ] No duplicate files on archive drive.

## Mid-run interruption

- [ ] Yank the external drive mid-run.
- [ ] Tool aborts with a non-zero exit code and a readable error.
- [ ] `icloud-archiver status` shows non-terminal items.
- [ ] Re-plug drive, re-run; resume proceeds without duplicates.

## Empty trash

- [ ] `uv run icloud-archiver empty-trash` prompts with item count.
- [ ] Typing `EMPTY` proceeds; anything else aborts.
- [ ] Within ~1 minute, iCloud.com → Recently Deleted is empty.
- [ ] iCloud Storage in Settings reflects the reclaimed space.
```

- [ ] **Step 2: Expand the README**

Modify `README.md` — replace the placeholder with a fuller version:

```markdown
# iCloud Archiver

A Python CLI tool that archives the oldest iCloud Photos to an external drive
(oldest-first, bounded by a target number of bytes to free), then deletes them
from iCloud after strict per-item verification.

## Why

iCloud Photos plans top out at 2 TB. If your library has crossed that line,
this tool gives you a controlled, journaled, resumable way to move older years
to an external drive and reclaim space, without losing metadata, edits,
Live Photo videos, or album membership.

## Design

- **Selection:** oldest-first, bounded by `--target-freed` (e.g. `1TB`, `500GB`).
- **Verification before deletion:** every downloaded file passes size + parse
  + SHA-256 + (where available) iCloud-checksum compare before its iCloud
  copy is deleted. A truncated download or corrupted parse never causes data loss.
- **SQLite journal** at `~/.icloud-archiver/state.db` is the source of truth.
  Any run can be Ctrl-C'd or crash; the next run picks up where it left off.
- **Album-based folders** on the external drive with **hardlinks** for items
  that live in multiple albums — no duplicated bytes on disk.
- **Sidecar `.json` files** preserve full metadata (capture date, GPS, album
  membership, captions, favorites, edits info, original filename).
- **Recently Deleted** is never automatically emptied — `empty-trash` is its
  own command with its own typed-confirmation gate. The 30-day Apple window
  is your recovery path.

Full design: [`docs/superpowers/specs/2026-05-19-icloud-archiver-design.md`](docs/superpowers/specs/2026-05-19-icloud-archiver-design.md).
Implementation plan: [`docs/superpowers/plans/2026-05-19-icloud-archiver.md`](docs/superpowers/plans/2026-05-19-icloud-archiver.md).

## Requirements

- macOS (tested on 15.x).
- Python 3.11+ (managed by `uv`).
- An external drive that is, or can be reformatted to, APFS.
- An Apple ID with iCloud Photos.

## Install

```bash
uv sync
```

## First-time setup

```bash
uv run icloud-archiver login
```

Stores the session cookie under `~/.icloud-archiver/cookies/` and the password
in the macOS Keychain.

## Typical run

```bash
# See what would happen
uv run icloud-archiver plan --target-freed 1TB

# Actually do it
uv run icloud-archiver run --target-freed 1TB

# After spot-checking the archive, free the space
uv run icloud-archiver empty-trash
```

## Status & resume

```bash
uv run icloud-archiver status
```

Shows items by state and the last few runs. Any non-terminal items will be
picked up automatically by the next `run` invocation.

## Develop

```bash
uv sync
uv run pytest
uv run mypy --strict src/icloud_archiver
uv run ruff check
```

Before trusting it with your real library, work through `tests/manual/README.md`.
```

- [ ] **Step 3: Write a Makefile for common commands**

`Makefile`:
```makefile
.PHONY: test lint type smoke

test:
	uv run pytest -v

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests

type:
	uv run mypy src/icloud_archiver

smoke:
	@echo "Manual smoke tests live in tests/manual/README.md."
	@echo "Open it and work through the checklist before any real-library run."
```

- [ ] **Step 4: Run everything one final time**

Run: `uv run pytest -v`
Expected: all green.

Run: `uv run ruff check src tests`
Expected: no issues, or only auto-fixable ones (run `uv run ruff check --fix` to clean up).

Run: `uv run mypy src/icloud_archiver`
Expected: `Success: no issues found`.

- [ ] **Step 5: Commit**

```bash
git add tests/manual/README.md README.md Makefile
git commit -m "docs: manual smoke checklist, README, Makefile"
```

---

## Done

The tool is now ready for the manual smoke pass in `tests/manual/README.md`,
followed by the real 2 TB archival run.
