"""Click CLI: login, disks, plan, run, status, empty-trash."""

import contextlib
import json
from datetime import UTC, datetime
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
    caffeinate_for_run,
    confirm_reformat,
    list_external_drives,
    needs_reformat,
    pick_drive_interactive,
    reformat_apfs,
)
from icloud_archiver.reporting import render_plan_markdown, render_run_summary
from icloud_archiver.types import ItemState


# Path helpers — separate functions so tests can monkeypatch them in one place.
def _state_path() -> Path:
    return state_dir() / "state.db"


def _plans_dir() -> Path:
    p = state_dir() / "plans"
    p.mkdir(exist_ok=True)
    return p


def _saved_email() -> str | None:
    """Read the most recently logged-in email from state_dir/config.json."""
    cfg = state_dir() / "config.json"
    if not cfg.exists():
        return None
    with contextlib.suppress(Exception):
        return str(json.loads(cfg.read_text()).get("email"))
    return None


def _save_email(email: str) -> None:
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
        drives = list_external_drives()
        new_drive = next(
            (d for d in drives if d.volume_name == drive.volume_name), None
        )
        if new_drive is None or needs_reformat(new_drive.fs):
            click.echo("Reformat did not yield an APFS volume.", err=True)
            raise SystemExit(1)
        drive = new_drive

    prompt = (
        f"Archive subdirectory name on '{drive.volume_name}' [default: iCloud-Archive]: "
    )
    subdir = input(prompt).strip() or "iCloud-Archive"
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
    """Dry-run: produce a plan of what would be archived."""
    target_bytes = parse_size(target_freed)
    archive_root, _drive = _interactive_picker()
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
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    plan_path = _plans_dir() / f"{ts}-plan.md"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(md)
    click.echo(md)
    click.echo(f"\nPlan written to {plan_path}")


@main.command()
@click.option("--target-freed", required=True, help="Bytes to free, e.g. 1TB or 500GB")
def run(target_freed: str) -> None:
    """Archive the oldest items until target_bytes is freed."""
    target_bytes = parse_size(target_freed)
    archive_root, _drive = _interactive_picker()
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
            bytes_pending_free=outcome.bytes_archived,
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
    """Permanently empty items archived by this tool from Recently Deleted."""
    journal = Journal.open(_state_path())
    counts = journal.items_by_state()
    eligible = counts.get("DELETED", 0)
    if eligible == 0:
        click.echo("Nothing to empty — no items in DELETED state in the journal.")
        return
    click.echo(
        f"Recently Deleted contains {eligible} items archived by this tool, "
        "all with verified local copies."
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
