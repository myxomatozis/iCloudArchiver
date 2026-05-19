"""Plan reports (markdown) and run summaries (terminal)."""

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
        f"- Projected free-space needed (x1.2): {_human(int(total * 1.2))}",
        "",
        "## Albums touched",
        "",
        *(f"- {a}" for a in albums),
        "",
    ]
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
    return "\n".join(
        [
            "Run complete.",
            f"  {archived} archived, {deleted} deleted",
            f"  {failed_verify} failed verify, {failed_download} failed download,"
            f" {skipped} skipped",
            f"  {_human(bytes_archived)} archived total",
            f"  {_human(bytes_pending_free)} now in Recently Deleted (not yet reclaimed)",
            "",
            "Recently Deleted holds purged items for 30 days. To reclaim space sooner,",
            "run `icloud-archiver empty-trash` once you've spot-checked the archive.",
        ]
    )
