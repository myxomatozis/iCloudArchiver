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
