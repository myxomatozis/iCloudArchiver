from pathlib import Path
from unittest.mock import MagicMock

import pytest

from icloud_archiver.preflight import (
    Drive,
    enough_free_space,
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
    assert needs_reformat("msdos_fat32")
    assert needs_reformat("msdos fat32")  # space variant normalizes to underscore


def test_needs_reformat_false_for_apfs_and_hfs() -> None:
    assert not needs_reformat("apfs")
    assert not needs_reformat("APFS")
    assert not needs_reformat("hfs+")
    assert not needs_reformat("journaled hfs+")


def test_list_external_drives_filters_system(monkeypatch: pytest.MonkeyPatch) -> None:
    # disk0 = internal system disk (partition at /  → filtered)
    # disk4 = traditional (non-APFS) external with a Partitions entry
    # disk5 = APFS container whose volumes live under APFSVolumes (the common
    #          modern case that was previously missed)
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
    <dict>
      <key>DeviceIdentifier</key><string>disk5</string>
      <key>Partitions</key>
      <array/>
      <key>APFSVolumes</key>
      <array>
        <dict>
          <key>DeviceIdentifier</key><string>disk5s1</string>
          <key>VolumeName</key><string>MyAPFSDrive</string>
          <key>MountPoint</key><string>/Volumes/MyAPFSDrive</string>
          <key>OSInternal</key><false/>
        </dict>
      </array>
    </dict>
  </array>
</dict>
</plist>"""

    def fake_run(*_args: object, **_kwargs: object) -> MagicMock:
        m = MagicMock()
        m.stdout = plist
        m.returncode = 0
        return m

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr(
        "icloud_archiver.preflight.detect_filesystem",
        lambda _mp: "apfs",
    )
    monkeypatch.setattr(
        "icloud_archiver.preflight._volume_stats",
        lambda _mp: (1_800_000_000_000, 2_000_000_000_000),
    )

    drives = list_external_drives()
    assert [d.volume_name for d in drives] == ["Samsung T7", "MyAPFSDrive"]
    assert drives[0].mount_point == Path("/Volumes/T7")
    assert drives[1].mount_point == Path("/Volumes/MyAPFSDrive")
    assert all(d.fs == "apfs" for d in drives)
    assert all(d.is_external for d in drives)


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
    with pytest.raises(SystemExit) as exc_info:
        pick_drive_interactive(drives)
    assert exc_info.value.code == 0


def test_enough_free_space_true_when_room(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("icloud_archiver.preflight._volume_stats", lambda _p: (2_000, 9_999))
    ok, free, required = enough_free_space(tmp_path, target_bytes=1_000)
    assert ok is True
    assert free == 2_000
    assert required == 1_200  # 1000 * 1.2


def test_enough_free_space_false_when_short(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("icloud_archiver.preflight._volume_stats", lambda _p: (1_000, 9_999))
    ok, _free, required = enough_free_space(tmp_path, target_bytes=1_000)
    assert ok is False
    assert required == 1_200


def test_enough_free_space_ok_at_exact_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("icloud_archiver.preflight._volume_stats", lambda _p: (1_200, 9_999))
    ok, _free, required = enough_free_space(tmp_path, target_bytes=1_000)
    assert ok is True
    assert required == 1_200
