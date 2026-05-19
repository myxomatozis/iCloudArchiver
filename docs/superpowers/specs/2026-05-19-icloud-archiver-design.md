# iCloud Archiver — Design Spec

**Date:** 2026-05-19
**Owner:** oleh.smirnov@icloud.com
**Status:** Design approved, ready for implementation planning

## 1. Problem

The iCloud Photos library is approaching its 2 TB plan limit. The goal is to free a configurable amount of space (initial target: ~1 TB) by archiving the oldest photos to an external drive and deleting them from iCloud.

Constraints established during brainstorming:

- The Photos library is **not present on this Mac**. iCloud Photos is accessed only via icloud.com / mobile devices. The tool must drive everything through the iCloud web API.
- Selection model: **oldest-first, bounded by a target number of bytes to free**.
- Safety model: **strict per-item verification before any deletion** from iCloud, with a journal that survives crashes.
- Must support **dry-run** to preview a run before any writes.
- Each item must be archived with its **full fidelity**: original, Live Photo paired video, edited version (if any), and a sidecar JSON containing capture date, GPS, album membership, captions, favorite flag, original filename.
- Files on the external drive are organized into **album folders**, with **hardlinks** for multi-album items so disk usage is not duplicated. Items in no album fall back to a date-based folder.
- The external drive is selected via an **interactive picker**; if its filesystem doesn't support hardlinks (i.e. not APFS or HFS+), the tool offers to reformat it as APFS after an explicit typed-confirmation gate.

## 2. Non-goals

- Not a general iCloud Photos backup / sync tool. One-shot archival only.
- Not a replacement for Photos.app. No editing, no UI, no library import.
- Not designed to run unattended on a schedule. Designed for human-supervised runs.
- Not designed for multiple iCloud accounts in a single run.
- Not designed to manage anything other than iCloud **Photos** (no Drive, Mail, Notes, etc.).

## 3. Architecture overview

A single Python 3.11+ CLI tool, packaged with `uv`, run from this Mac.

```
iCloud  ←──HTTPS──┐
                  │   (pyicloud-ipd: auth, enumerate, delete)
                  │   (icloudpd downloader: download bytes)
                  ▼
       ┌──────────────────────┐
       │   icloud-archiver    │   Python 3.11+
       │  (selection, verify, │
       │   journal, organize, │
       │   delete-gate, dry)  │
       └──────────────────────┘
            │            │
            ▼            ▼
   External drive    SQLite journal
   (album folders   (~/.icloud-archiver/
   + hardlinks +     state.db, run logs)
   sidecars)
```

### 3.1 Dependencies

Pinned in `pyproject.toml`:

- `pyicloud-ipd` — Apple auth, photo enumeration, deletion. The actively-maintained fork used by `icloudpd`.
- `icloudpd` — used as a library for its download internals (resume-aware, MIME-aware, Live Photo pairing).
- `click` — CLI framework.
- `tqdm` — progress bars.
- `structlog` — structured logs (JSONL to file, pretty to terminal).
- `keyring` — macOS Keychain access for storing the Apple ID password.
- `Pillow` — image header parse-verify.
- `pytest`, `mypy`, `ruff` — dev tools.

### 3.2 Filesystem layout

**Code repo:** `/Users/oleh.smirnov/Work/iCloudArchiver/`

**State directory:** `~/.icloud-archiver/`
- `state.db` — SQLite journal (see §6).
- `cookies/` — Apple session cookies (managed by `pyicloud-ipd`, perms `0600`).
- `config.json` — last-used Apple ID email and minor config.
- `logs/<run_id>.jsonl` — structured run logs.
- `plans/<timestamp>.md` — dry-run plan reports.

**Archive drive (chosen interactively):** `/Volumes/<chosen>/<subdir>/`
- Subdirectory name prompted at picker time, default `iCloud-Archive`.
- Inside that root, items are placed under album folders or under date folders for un-albumed items (see §5.4).
- A `.scratch/` directory at the root holds partial downloads during a run.

### 3.3 Top-level commands

- `icloud-archiver login` — first-time / re-auth interactive flow.
- `icloud-archiver disks` — print the candidate external drives (no side effects).
- `icloud-archiver plan --target-freed <SIZE>` — dry-run, produces a plan report.
- `icloud-archiver run --target-freed <SIZE>` — performs the archival.
- `icloud-archiver status` — summarize the journal: items by state, last run, resumable items.
- `icloud-archiver empty-trash` — explicitly empty Recently Deleted (only items archived by this tool).

`<SIZE>` is parsed as a human-readable string (`1TB`, `500GB`, `250GB`).

## 4. Module layout

```
src/icloud_archiver/
├── cli.py              # Click commands; thin glue to modules below
├── config.py           # Resolves state dir, target freed, dry-run flag, parsed sizes
├── auth.py             # Wraps pyicloud-ipd login + 2FA + cookie persistence
├── catalog.py          # Enumerate photos sorted by created_at ASC; yields CatalogItem
├── selector.py         # Pure: given iterator + target_bytes + journal,
│                       #   yield items to archive until cumulative size ≥ target
├── downloader.py       # For one CatalogItem: download original + Live MOV + edits;
│                       #   write sidecar JSON. Uses icloudpd's download primitives.
├── verifier.py         # Strict per-file checks: size, parse, sha256
├── organizer.py        # Place verified files into album folders with hardlinks
├── deleter.py          # iCloud delete API; idempotent
├── journal.py          # SQLite wrapper: open, migrate, record events, query resume
├── preflight.py        # Disk picker, mount/external/system checks, FS probe,
│                       #   reformat-to-APFS prompt, free-space check, caffeinate spawn
└── reporting.py        # Plan reports, run summaries

tests/
├── unit/               # selector, verifier, organizer, journal, preflight (mocked)
├── integration/        # end-to-end with FakeICloudPhotos against fixture bytes
├── fixtures/           # sample HEIC/JPEG/MP4 files for verifier + integration
└── manual/README.md    # human smoke checklist before the real 2 TB run
```

### 4.1 Shared data type

```python
@dataclass(frozen=True)
class CatalogItem:
    asset_id: str               # iCloud's stable id
    created_at: datetime        # capture time (UTC)
    size_bytes: int             # original asset size as reported by iCloud
    albums: list[str]           # sorted alphabetically (case-insensitive) for determinism;
                                #   albums[0] = primary placement folder.
                                #   Apple "folders containing albums" are flattened to
                                #   "Parent/Child" strings.
    original_filename: str
    has_live_photo: bool
    has_edits: bool
    mime_type: str
    icloud_checksum: str | None  # None when Apple doesn't expose one
```

`CatalogItem` is produced by `catalog`, consumed by every other module. It is *not* mutated — it is the immutable description of what to archive. Mutable per-item state lives in the journal.

## 5. Run flow

### 5.1 End-to-end

```
1. preflight (phase A — drive readiness)
   ├── disk picker (interactive) — see §5.2
   ├── mount/external/system checks for picked volume
   ├── filesystem probe (APFS pass; HFS+ pass with warning; else reformat prompt — §5.3)
   ├── ~/.icloud-archiver/state.db opened or migrated to current schema
   └── caffeinate -dimsu spawned as child to prevent sleep

2. auth.load_session()
   ├── if cookie valid → reuse
   └── else → exit with "run `icloud-archiver login` first"

3. journal.open() and resume detection
   ├── find non-terminal items; route by state — see §6.2
   └── start a new run_id row in `runs`

4. catalog.iter_oldest_first()
   └── yields CatalogItem stream sorted by created_at ASC, lazily

5. selector pre-pass: enumerate items oldest-first, summing size_bytes
   until the cumulative total ≥ target_bytes. Produce the list of asset_ids
   that will be archived this run, plus projected_download_size.

6. preflight (phase B — capacity check)
   └── free space on archive drive ≥ projected_download_size × 1.2;
       abort with a clear error if not.

7. for item in selected_items:
     ├── journal.record(item, state=PLANNED)
     ├── downloader.fetch(item, scratch_dir) → DownloadResult
     ├── journal.record(item, state=DOWNLOADED)
     ├── verifier.verify(item, result)        ← strict checks; see §7
     │     fail → journal FAILED_VERIFY, alert, continue
     ├── organizer.place(item, result, archive_root) → final_paths
     ├── journal.record(item, state=ARCHIVED, paths=final_paths)
     ├── deleter.delete(item)                 ← iCloud API
     └── journal.record(item, state=DELETED, bytes_freed_running_total=…)

8. final report
   ├── totals (items archived, bytes archived, bytes pending-free)
   ├── skipped + reason
   ├── failed + reason
   └── reminder about Recently Deleted (§7.3)
```

### 5.2 Disk picker

Triggered at the start of every `run` and `plan`. There is no `--archive-root` flag; the picker is the only way to choose a target. (Removed in design discussion — it was a silent-archive-to-wrong-path footgun.)

Probe via `diskutil list -plist` + `system_profiler SPUSBDataType`. Filter to mounted, external, non-system volumes. Render:

```
External drives available:

  [1]  Samsung T7         APFS    1.8 TB free / 2.0 TB    /Volumes/T7
  [2]  WD Elements        exFAT   3.9 TB free / 4.0 TB    /Volumes/Elements   ⚠ will need reformat
  [3]  LaCie Rugged       HFS+    1.1 TB free / 2.0 TB    /Volumes/LaCie

Select target drive [1-3], or 'q' to quit:
```

After selection: `Archive subdirectory name on '<NAME>' [default: iCloud-Archive]:`. The archive root becomes `/Volumes/<NAME>/<subdir>/`.

Edge cases:

- **No external drives** → exit with explanatory message.
- **One external drive** → still display it for confirmation; no silent auto-pick.
- **System / internal volumes** → filtered out entirely.
- **Network volumes (SMB/AFP)** → shown but flagged; hardlinks may misbehave; allowed but with warning.

`icloud-archiver disks` runs only the probe + render, no side effects.

### 5.3 Filesystem probe and reformat prompt

- **APFS** → continue silently.
- **HFS+** → continue, log a note that APFS is preferred but hardlinks work.
- **exFAT / FAT32 / NTFS** → interactive prompt:

  ```
  Drive '<NAME>' (<disk identifier>) is exFAT.
  This archive uses hardlinks for multi-album items, which require APFS or HFS+.

  Reformat <NAME> as APFS now?
     [type 'ERASE <NAME>' to confirm]   (--yes-erase to skip prompt)

  ⚠️  THIS WILL PERMANENTLY DELETE EVERYTHING ON <NAME>.
  ⚠️  Other drives are NOT affected.
  ⚠️  This action cannot be undone.
  ```

Safety details:

- Requires typing `ERASE <NAME>` exactly. No bare `y`.
- Always re-prints the disk identifier (e.g. `disk4s2`) and volume name immediately before invoking `diskutil`.
- `--yes-erase` skips the typed-confirmation gate; default is interactive.
- If the volume is non-empty, an additional warning lists visible top-level entries before the gate.
- Operates only on the **volume identifier** (e.g. `disk4s2`), never the whole physical disk (`disk4`).
- After format, re-probe; if still not APFS, bail with the underlying `diskutil` error.

### 5.4 Album folder organization

Per-item:

1. Compute the **primary album** = `albums[0]` if non-empty (deterministic — alphabetical, see CatalogItem in §4.1), else `_NoAlbum/YYYY/MM/` derived from `created_at`.
2. Primary file = `<archive_root>/<primary_album>/<original_filename>`. Filename collisions resolved with `_<short_asset_id>` suffix.
3. For each additional album in `albums[1:]`, create `os.link(primary, <archive_root>/<other_album>/<original_filename>)`.
4. Live Photo paired video (`.MOV`) and edited version go alongside the primary file with derived suffixes (`<base>_LIVE.MOV`, `<base>_EDITED.<ext>`). Each is also hardlinked into each additional album folder.
5. Sidecar `<base>.json` written next to the primary file, and **also hardlinked into each additional album folder** so each folder is self-describing when browsed in isolation.

Sidecar JSON schema:

```json
{
  "asset_id": "...",
  "original_filename": "IMG_1234.HEIC",
  "created_at": "2014-08-23T15:42:01Z",
  "mime_type": "image/heic",
  "size_bytes": 4823942,
  "sha256": "...",
  "icloud_checksum": "...",
  "albums": ["Family/Italy 2014", "Highlights"],
  "favorite": true,
  "captions": "Sunset from the balcony",
  "gps": {"lat": 43.7696, "lon": 11.2558, "alt_m": 50.0},
  "has_live_photo": true,
  "has_edits": false,
  "archived_at": "2026-05-20T11:02:33Z",
  "archived_by_run": "01HZ..."
}
```

### 5.5 Dry-run (`plan`)

Exercises steps 1–5 of the run flow but substitutes no-op downloader, organizer, and deleter that record the *intended* action. Produces:

- `~/.icloud-archiver/plans/<timestamp>.md` — markdown report: count, total bytes, oldest/newest dates, albums touched, projected free-space requirement, projected hardlink count.
- Same content also printed to the terminal.
- No iCloud writes. No archive-drive writes beyond reading the picker.

## 6. State, journal, and resume

### 6.1 SQLite schema (`~/.icloud-archiver/state.db`)

```sql
CREATE TABLE runs (
  run_id            TEXT PRIMARY KEY,           -- ULID
  started_at        TEXT NOT NULL,              -- ISO 8601 UTC
  ended_at          TEXT,                       -- null while running
  target_bytes      INTEGER NOT NULL,
  dry_run           INTEGER NOT NULL,           -- 0/1
  archive_root      TEXT NOT NULL,
  ended_status      TEXT                        -- 'completed' | 'aborted' | 'crashed'
);

CREATE TABLE items (
  asset_id          TEXT PRIMARY KEY,           -- iCloud's stable id
  first_seen_run    TEXT NOT NULL,
  created_at        TEXT NOT NULL,              -- photo capture time
  size_bytes        INTEGER NOT NULL,           -- iCloud-reported original size
  state             TEXT NOT NULL,              -- see §6.2
  primary_path      TEXT,                       -- final real file on archive drive
  hardlink_paths    TEXT,                       -- JSON array of additional paths
  sha256            TEXT,                       -- of original file post-download
  icloud_checksum   TEXT,
  error             TEXT,                       -- last error message if FAILED_*
  updated_at        TEXT NOT NULL
);

CREATE TABLE item_events (                      -- append-only audit log
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id            TEXT NOT NULL,
  asset_id          TEXT NOT NULL,
  at                TEXT NOT NULL,
  from_state        TEXT,
  to_state          TEXT NOT NULL,
  detail            TEXT                        -- JSON: paths, bytes, error
);

CREATE INDEX items_state_idx ON items(state);
CREATE INDEX items_created_idx ON items(created_at);
```

### 6.2 Item state machine

```
                  PLANNED
                     │
                     ▼
                DOWNLOADING ───────┐ (crash/error)
                     │             ▼
                     │      FAILED_DOWNLOAD ──→ (next run retries)
                     ▼
                DOWNLOADED
                     │
                     ▼
                 VERIFYING ────────┐
                     │             ▼
                     │       FAILED_VERIFY ──→ (alert; iCloud copy untouched)
                     ▼
                  VERIFIED
                     │
                     ▼
                ORGANIZING
                     │
                     ▼
                 ARCHIVED
                     │
                     ▼
                 DELETING
                     │
                     ▼
                  DELETED ←── terminal success

   SKIPPED — opted out (e.g. shared library, can't be deleted)
```

Terminal states: `DELETED`, `SKIPPED`, `FAILED_VERIFY`.

### 6.3 Resume model

On every `run` start, before entering normal selection:

1. Query items in non-terminal states from the journal.
2. Route each by state:
   - `PLANNED` / `DOWNLOADING` / `FAILED_DOWNLOAD` → delete any partials in `.scratch/`, return to `PLANNED`, redownload.
   - `DOWNLOADED` / `VERIFYING` → rerun verification.
   - `VERIFIED` / `ORGANIZING` → rerun organize (idempotent: hardlinks skipped if present, primary path checked by inode).
   - `ARCHIVED` / `DELETING` → call iCloud delete (effectively idempotent; deleting an already-trashed item is logged and treated as success).
3. Then enter normal selection for new items.

`FAILED_VERIFY` items are deliberately *not* retried automatically. They require human inspection and explicit re-queuing via a future maintenance command (not in v1 scope; for v1, manual SQL is acceptable).

## 7. Safety model

### 7.1 Verify-before-delete chain

For each item, in order. Any failure stops the chain at that item.

1. **Download integrity at write time.**
   - Bytes written to `<archive_root>/.scratch/<asset_id>.partial`.
   - On EOF: `fsync`, then rename to `<asset_id>.complete`.
   - Any downloader error → delete partials, mark `FAILED_DOWNLOAD`, continue.

2. **File-level reverify (verifier).**
   - **Size check**: `os.stat(file).st_size == catalog_item.size_bytes`.
   - **Parse check**:
     - HEIC / JPEG → `PIL.Image.open(...).verify()`.
     - MP4 / MOV → top-level atom walk; require `ftyp`, `moov`, `mdat` with lengths summing to file size.
     - Other → magic-byte check at minimum.
   - **Hash check**: SHA-256 of the file, stored in `items.sha256`. If `icloud_checksum` is present, compare.
   - All three must pass. Any failure → `FAILED_VERIFY`, no delete, alert.

3. **Place on disk (organizer).**
   - Same-filesystem rename of the verified file from `.scratch/` to its primary path in the primary album folder.
   - For each additional album, `os.link(primary, link_path)`.
   - Write sidecar JSON next to the primary path.
   - `fsync` the primary directory entry.
   - Read first 4 KB of the placed primary file back and compare against the in-memory hash of the same offset captured during verify. Mismatch → `FAILED_VERIFY`, leave iCloud copy alone.

4. **Mark `ARCHIVED`** in journal with all final paths and `sha256`.

5. **Delete from iCloud (`deleter.delete`).**
   - `pyicloud-ipd` photo-delete endpoint.
   - On success → mark `DELETED`, add `size_bytes` to run's `bytes_freed` running total.
   - On failure → mark `DELETING` with the error; resume on next run.

### 7.2 Pre-flight safety nets

- Archive drive is the picked external volume, not internal SSD.
- Free space ≥ 1.2 × projected download size (selector pre-computes the sum before any download starts).
- APFS or HFS+ (or post-format APFS).
- `caffeinate -dimsu` spawned as child for the duration of the run.

### 7.3 Recently Deleted handling

Apple's delete moves items to **Recently Deleted** for 30 days; they continue to count against the 2 TB quota until purged.

Behavior:

- `run` **never** empties Recently Deleted. The 30-day window is the recovery path.
- `icloud-archiver empty-trash` is the only command that empties it.
- `empty-trash` prompts:
  ```
  Recently Deleted contains <N> items (<size> total) that were archived by this tool.
  All of these items have a verified local copy at <archive_root>.

  Permanently empty Recently Deleted? Type 'EMPTY' to confirm:
  ```
- `empty-trash` operates only on items that this tool's journal records as `DELETED`. Items the user trashed by other means are left alone.

### 7.4 Failure-mode matrix

| Failure                          | Detected by      | Outcome                                           |
|----------------------------------|------------------|---------------------------------------------------|
| Truncated download               | size check       | retry on next run; iCloud copy untouched          |
| Corrupted parse                  | Pillow / atom    | `FAILED_VERIFY`; iCloud copy untouched, alert     |
| Wrong checksum vs Apple's        | hash compare     | `FAILED_VERIFY`; iCloud copy untouched, alert     |
| Disk full mid-write              | OS error         | partial file deleted; `FAILED_DOWNLOAD`; retry    |
| External drive unplugged mid-run | OS error         | abort run; resume after replug                    |
| Apple session expired mid-run    | API 401          | abort gracefully; journal preserved; relog        |
| Apple delete fails for one item  | API error        | item stays `ARCHIVED`; retried next run           |
| User Ctrl-C                      | SIGINT handler   | finish current item then stop; journal consistent |

## 8. Authentication

Apple auth is the most fragile moving part. Strategy: **don't reinvent it** — lean on `pyicloud-ipd`'s implementation.

### 8.1 First-time (`icloud-archiver login`)

1. Prompt for Apple ID email.
2. Prompt for password via `getpass` (never echoed, never logged).
3. `pyicloud-ipd` triggers Apple 2FA; a 6-digit code appears on trusted devices.
4. Prompt for the code.
5. On success, session cookie persisted to `~/.icloud-archiver/cookies/` (perms `0600`).
6. Email stored in `~/.icloud-archiver/config.json`. Password stored in **macOS Keychain** via `keyring` library at `icloud-archiver:<email>`. Never written to disk in any other form.

### 8.2 Subsequent runs

1. `auth.load_session()` reads the cookie file; if `pyicloud-ipd` reports it valid, no prompt.
2. If invalid/expired, the `run` and `plan` commands refuse to start with `"run `icloud-archiver login` first"`. Rationale: a long unattended run hitting a 2FA prompt mid-download is worse than failing fast.
3. Cookie file permissions re-enforced to `0600` on every read.

### 8.3 Failure modes

- Wrong password → keychain entry left alone; user re-prompted; keychain updated only on a successful login.
- 2FA code rejected → up to 3 retries, then exit. No backoff (Apple's rate-limiting is the source of truth).
- Account-level block → surface Apple's exact error, exit, recommend signing in via icloud.com.

### 8.4 Explicit non-features

- No app-specific passwords.
- No persisting password to any file.
- No "remember me" beyond Apple's own session cookie.

## 9. Testing strategy

### 9.1 Unit (fast, hermetic, every change)

Target modules: `selector`, `verifier`, `organizer`, `journal`, `preflight`, `reporting`, sidecar writer.

- `selector`: synthetic catalog → "select until N bytes" returns the expected prefix. Edge cases: empty catalog, target > total, target = 0, already-DELETED items skipped.
- `verifier`: fixture files (valid HEIC, valid MP4, truncated JPEG, zero-byte, JPEG with last bytes stripped). Each gets the expected pass/fail.
- `organizer`: against `tmp_path` — multi-album item → primary + N hardlinks + sidecar. Idempotency: run twice, identical end state.
- `journal`: SQLite against `:memory:`. Exercise every state transition; assert resume routing.
- `preflight`: `diskutil` output mocked from captured real samples. Cover APFS pass, exFAT → reformat prompt, no-disks, multi-disks → picker. `diskutil eraseDisk` mocked — never invoked in tests.

### 9.2 Integration (FakeICloudPhotos)

A `FakeICloudPhotos` implements the small interface between us and `pyicloud-ipd` (enumerate, get-download-url, delete, get-checksum). It serves bytes from `tests/fixtures/`, ~50 sample assets with varied dates/sizes/albums/Live Photos/edits.

Test scenarios end-to-end against the fake:

- Happy path: target = total → all items `DELETED`.
- Partial target: target = 60% of total → oldest N items archived; rest untouched.
- Mid-run crash: `SystemExit` after `DOWNLOADED` for one item; restart; assert consistent terminal state, no duplicate disk files.
- Verify failure: fake serves a truncated copy of one asset; that asset ends `FAILED_VERIFY`, iCloud copy not deleted, others succeed.
- Delete failure: fake raises on delete for one asset; local archive intact; item stays `ARCHIVED`; next run retries delete successfully.
- Dry-run: no bytes written outside `~/.icloud-archiver/plans/`; no delete calls on fake.

### 9.3 Manual smoke (human, against real account, before the real 2 TB run)

Documented in `tests/manual/README.md`:

- `login` against real account; confirm 2FA; confirm cookie reuse next day.
- `plan --target-freed 1GB`; eyeball plan markdown.
- `run --target-freed 1GB` against a *test* external drive. Confirm files + hardlinks + sidecars + journal terminal + Recently Deleted population.
- `empty-trash` on a 10-item batch; confirm iCloud quota reclaimed in Settings.
- Yank the external drive mid-run; confirm graceful abort. Replug; resume.
- A reformat dry-run on a sacrificial USB stick — never the real archive drive.

### 9.4 CI / dev loop

- `uv run pytest` — layers 1 and 2.
- `make smoke` — print the manual checklist with checkboxes.
- `mypy --strict` on `src/icloud_archiver/`.
- `ruff` for lint.

### 9.5 Not tested

- Apple's actual API (we trust `pyicloud-ipd`).
- `diskutil eraseDisk` actually working (mocked at the boundary).
- Multi-day stability — covered only by manual smoke + resume design.

## 10. Open questions / explicit deferrals

- **Re-queue after `FAILED_VERIFY`**: v1 leaves these to manual SQL. A `reset-item` subcommand may be added later if it happens often.
- **Multiple-Apple-ID runs**: out of scope.
- **Shared Library items**: behavior depends on `pyicloud-ipd` exposure. v1 will mark them `SKIPPED` if delete is rejected, and surface the count in the run summary.
- **Resume across reboots**: covered by SQLite + idempotent organizer + scratch cleanup. No special handling beyond that.
- **Future un-archive**: not in scope. Sidecars are designed so a future tool could re-upload to iCloud and re-tag with albums.
