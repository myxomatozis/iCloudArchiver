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
