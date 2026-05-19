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
uv run mypy src/icloud_archiver
uv run ruff check
```

Before trusting it with your real library, work through `tests/manual/README.md`.
