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
    plans = list((tmp_path / "plans").glob("*.md"))
    assert len(plans) == 1
