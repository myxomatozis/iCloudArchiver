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
    internal_drive,
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
    with contextlib.suppress(OSError, json.JSONDecodeError):
        return str(json.loads(cfg.read_text()).get("email"))
    return None


def _save_email(email: str) -> None:
    cfg = state_dir() / "config.json"
    cfg.write_text(json.dumps({"email": email}))


def _build_client() -> ICloudPhotos:
    email = _saved_email()
    if not email:
        raise click.ClickException("no saved email; run `icloud-archiver login` first")
    try:
        svc = load_session(state_dir() / "cookies", email=email)
    except SessionUnavailable as exc:
        raise click.ClickException(str(exc)) from exc
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
        new_drive = next((d for d in drives if d.volume_name == drive.volume_name), None)
        if new_drive is None or needs_reformat(new_drive.fs):
            click.echo("Reformat did not yield an APFS volume.", err=True)
            raise SystemExit(1)
        drive = new_drive

    prompt = f"Archive subdirectory name on '{drive.volume_name}' [default: iCloud-Archive]: "
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
    email = interactive_login(state_dir() / "cookies")
    _save_email(email)
    click.echo(f"Remembered {email} for subsequent runs.")


@main.command()
def disks() -> None:
    """Print drives that could be used as archive targets."""
    drives = [*list_external_drives(), internal_drive()]
    for i, d in enumerate(drives, start=1):
        if needs_reformat(d.fs):
            flag = "  ⚠ reformat needed"
        elif not d.is_external:
            flag = "  (internal)"
        else:
            flag = ""
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
    with Journal.open(_state_path()) as journal:
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

    # Also save a machine-readable JSON so `run --from-plan` can skip re-scanning.
    json_path = plan_path.with_suffix(".json")
    json_path.write_text(
        json.dumps(
            {
                "version": 2,
                "target_bytes": target_bytes,
                "archive_root": str(archive_root),
                "created_at": ts,
                "plan_run_id": outcome.plan_run_id,
                "item_count": len(outcome.plan_items),
            },
            indent=2,
        )
    )

    click.echo(md)
    click.echo(f"\nPlan written to {plan_path}")
    click.echo(
        f"Plan JSON written to {json_path}  "
        "(pass to `run --from-plan` to skip re-scanning; item details are in the journal DB)"
    )


@main.command()
@click.option("--target-freed", default=None, help="Bytes to free, e.g. 1TB or 500GB")
@click.option(
    "--from-plan",
    "plan_file",
    default=None,
    type=click.Path(exists=True, path_type=Path),
    help="Path to a JSON plan file produced by `plan`; skips iCloud scanning.",
)
def run(target_freed: str | None, plan_file: Path | None) -> None:
    """Archive the oldest items until target_bytes is freed.

    Either --target-freed or --from-plan must be provided.  When --from-plan is
    given the iCloud catalog scan is skipped and the items listed in the plan
    file are archived directly.
    """
    if plan_file is None and target_freed is None:
        raise click.UsageError("provide --target-freed or --from-plan (see --help)")

    plan_run_id: str | None = None
    plan_file_name: str | None = None
    if plan_file is not None:
        plan_data = json.loads(plan_file.read_text())
        target_bytes = plan_data["target_bytes"]
        plan_run_id = plan_data["plan_run_id"]
        plan_file_name = plan_file.name
    else:
        assert target_freed is not None  # checked above
        target_bytes = parse_size(target_freed)

    archive_root, _drive = _interactive_picker()
    client = _build_client()

    with Journal.open(_state_path()) as journal:
        preselected = None
        if plan_run_id is not None:
            preselected = journal.items_for_run(plan_run_id)
            click.echo(
                f"Loaded {len(preselected)} items from {plan_file_name} — skipping iCloud scan."
            )

        sleep_block = caffeinate_for_run()
        try:
            outcome = run_archival(
                client=client,
                journal=journal,
                archive_root=archive_root,
                target_bytes=target_bytes,
                dry_run=False,
                preselected=preselected,
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
            bytes_pending_free=outcome.bytes_deleted,
        )
    )


@main.command()
def status() -> None:
    """Show journal stats: items by state, recent runs."""
    with Journal.open(_state_path()) as journal:
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
    with Journal.open(_state_path()) as journal:
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


@main.command()
@click.argument("asset_ids", nargs=-1)
@click.option(
    "--all-deleted",
    is_flag=True,
    help="Reset every item currently in the DELETED state.",
)
def reset(asset_ids: tuple[str, ...], all_deleted: bool) -> None:
    """Clear journal state for archived items so they can be re-archived.

    Use this after restoring photos in iCloud. It edits only the local journal
    (state.db) — it does NOT touch iCloud. After a reset the next `run` will
    re-download, re-verify and re-delete those photos from iCloud.

    Pass one or more ASSET_IDS, or use --all-deleted (not both).
    """
    if bool(asset_ids) == all_deleted:
        raise click.UsageError("provide either ASSET_IDS or --all-deleted (not both)")

    with Journal.open(_state_path()) as journal:
        if all_deleted:
            targets = journal.asset_ids_in_state(ItemState.DELETED)
            if not targets:
                click.echo("Nothing to reset — no items in DELETED state.")
                return
            click.echo(
                f"This will reset {len(targets)} DELETED item(s). The next `run` will "
                "re-download and re-delete them from iCloud."
            )
            if input("Type 'RESET' to confirm: ").strip() != "RESET":
                click.echo("Aborted.")
                return
        else:
            targets = list(asset_ids)
        removed = journal.reset_items(targets)

    click.echo(f"Reset {removed} item(s). Re-run `run` to archive them again.")
    if not all_deleted and removed < len(targets):
        click.echo(f"({len(targets) - removed} of the given IDs were not in the journal.)")


if __name__ == "__main__":
    main()
