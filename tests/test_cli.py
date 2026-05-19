import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from icloud_archiver import cli as cli_mod
from icloud_archiver.cli import main
from icloud_archiver.preflight import Drive
from icloud_archiver.types import CatalogItem
from tests.fakes import FakeAsset, FakeICloudPhotos


def test_help_lists_subcommands() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    for cmd in ("login", "disks", "plan", "run", "status", "empty-trash"):
        assert cmd in result.output


def test_disks_subcommand_runs_without_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_mod, "list_external_drives", lambda: [])
    runner = CliRunner()
    result = runner.invoke(main, ["disks"])
    assert result.exit_code == 0
    assert "No external drives mounted" in result.output


def test_plan_writes_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    monkeypatch.setattr(cli_mod, "pick_drive_interactive", lambda _drives: drive)
    monkeypatch.setattr("builtins.input", lambda _p="": "iCloud-Archive")

    fake_assets = [
        FakeAsset(
            item=CatalogItem(
                asset_id="a",
                created_at=datetime(2014, 1, 1, tzinfo=UTC),
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
    plans_md = list((tmp_path / "plans").glob("*.md"))
    assert len(plans_md) == 1
    plans_json = list((tmp_path / "plans").glob("*.json"))
    assert len(plans_json) == 1
    data = json.loads(plans_json[0].read_text())
    assert data["version"] == 2
    assert data.get("plan_run_id")  # non-empty string
    assert data["item_count"] == 1
    assert "items" not in data  # item details are in the journal, not embedded here
    assert "from-plan" in result.output  # hint printed


def _make_drive(tmp_path: Path) -> Drive:
    drive = Drive(
        device_id="disk4s2",
        volume_name="T7",
        mount_point=tmp_path / "fake_mount",
        fs="apfs",
        free_bytes=10_000_000_000,
        total_bytes=20_000_000_000,
        is_external=True,
    )
    drive.mount_point.mkdir(exist_ok=True)
    return drive


def _make_fake_asset(asset_id: str = "a") -> FakeAsset:
    return FakeAsset(
        item=CatalogItem(
            asset_id=asset_id,
            created_at=datetime(2014, 1, 1, tzinfo=UTC),
            size_bytes=1000,
            albums=["X"],
            original_filename=f"{asset_id}.HEIC",
            has_live_photo=False,
            has_edits=False,
            mime_type="image/heic",
            icloud_checksum=None,
        ),
        original_bytes=b"X" * 1000,
    )


def test_run_errors_without_target_or_plan() -> None:
    """run with no options at all should fail with a usage error."""
    runner = CliRunner()
    result = runner.invoke(main, ["run"])
    assert result.exit_code != 0
    assert "from-plan" in result.output.lower() or "target-freed" in result.output.lower()


def test_run_from_plan_skips_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """run --from-plan (v2) reads items from the journal without touching the iCloud catalog."""
    from icloud_archiver.journal import Journal as _Journal
    from icloud_archiver.types import ItemState, RunStatus

    drive = _make_drive(tmp_path)
    monkeypatch.setattr(cli_mod, "list_external_drives", lambda: [drive])
    monkeypatch.setattr(cli_mod, "pick_drive_interactive", lambda _drives: drive)
    monkeypatch.setattr("builtins.input", lambda _p="": "iCloud-Archive")
    monkeypatch.setattr(cli_mod, "_state_path", lambda: tmp_path / "state.db")

    # Simulate a plan run: write item into the journal in PLANNED state.
    item = _make_fake_asset("b").item
    db_journal = _Journal.open(tmp_path / "state.db")
    plan_run_id = db_journal.start_run(
        target_bytes=5000, dry_run=True, archive_root=str(drive.mount_point / "iCloud-Archive")
    )
    db_journal.upsert_item(item, plan_run_id, ItemState.PLANNED)
    db_journal.end_run(plan_run_id, RunStatus.COMPLETED)
    db_journal.close()

    # Write a v2 plan JSON referencing the journal run.
    plan_json = {
        "version": 2,
        "target_bytes": 5000,
        "archive_root": str(drive.mount_point / "iCloud-Archive"),
        "created_at": "20250101T000000Z",
        "plan_run_id": plan_run_id,
        "item_count": 1,
    }
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan_json))

    # The fake client has the asset so the archival pipeline can process it;
    # what matters is that iter_oldest_first is never called.
    scanned: list[str] = []
    fake_client = FakeICloudPhotos(assets=[_make_fake_asset("b")])
    original_iter = fake_client.iter_oldest_first

    def spy_iter():
        scanned.append("scanned")
        return original_iter()

    fake_client.iter_oldest_first = spy_iter  # type: ignore[method-assign]
    monkeypatch.setattr(cli_mod, "_build_client", lambda: fake_client)
    monkeypatch.setattr(cli_mod, "caffeinate_for_run", lambda: _NullSleepBlock())

    runner = CliRunner()
    result = runner.invoke(main, ["run", "--from-plan", str(plan_path)])
    assert result.exit_code == 0, result.output
    assert scanned == [], "iter_oldest_first should NOT have been called when --from-plan is used"
    assert "skipping icloud scan" in result.output.lower()


class _NullSleepBlock:
    """Stand-in for the caffeinate process returned by caffeinate_for_run."""

    def terminate(self) -> None:
        pass
