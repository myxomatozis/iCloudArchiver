"""Disk picker, filesystem probe, reformat prompt, free-space check, sleep prevention."""

import os
import plistlib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_REFORMAT_FS = frozenset({"exfat", "msdos", "fat32", "ntfs", "msdos_fat32"})


@dataclass(frozen=True)
class Drive:
    device_id: str
    volume_name: str
    mount_point: Path
    fs: str
    free_bytes: int
    total_bytes: int
    is_external: bool


def needs_reformat(fs: str) -> bool:
    return fs.lower().replace(" ", "_") in _REFORMAT_FS


def detect_filesystem(mount_point: Path) -> str:
    """Run `diskutil info -plist <mount>` and pull `FilesystemType`."""
    res = subprocess.run(
        ["diskutil", "info", "-plist", str(mount_point)],
        capture_output=True,
        check=True,
    )
    data = plistlib.loads(res.stdout)
    return str(data.get("FilesystemType", "")).lower()


def _volume_stats(mount_point: Path) -> tuple[int, int]:
    """Return (free_bytes, total_bytes) using statvfs."""
    s = os.statvfs(mount_point)
    return s.f_bavail * s.f_frsize, s.f_blocks * s.f_frsize


def enough_free_space(archive_root: Path, target_bytes: int) -> tuple[bool, int, int]:
    """Check the destination volume can hold the projected download.

    Returns (ok, free_bytes, required_bytes). `required` is `target_bytes`
    scaled by the same 1.2x headroom factor `orchestrator.run_archival` uses.
    """
    free, _total = _volume_stats(archive_root)
    required = int(target_bytes * 1.2)
    return free >= required, free, required


def list_external_drives() -> list[Drive]:
    """Probe `diskutil list -plist` and return mounted, non-system volumes.

    Handles both traditional partition-based disks (``Partitions`` key) and
    APFS containers, whose logical volumes appear under ``APFSVolumes``.
    """
    res = subprocess.run(["diskutil", "list", "-plist"], capture_output=True, check=True)
    data = plistlib.loads(res.stdout)
    out: list[Drive] = []
    for disk in data.get("AllDisksAndPartitions", []):
        # APFS containers expose mounted volumes under APFSVolumes, not Partitions.
        candidates = disk.get("Partitions", []) + disk.get("APFSVolumes", [])
        for part in candidates:
            if part.get("OSInternal", False):
                continue
            mp = part.get("MountPoint")
            if not mp:
                continue
            mount_point = Path(mp)
            if mount_point == Path("/"):
                continue
            if not mount_point.is_absolute() or not str(mount_point).startswith("/Volumes/"):
                continue
            try:
                fs = detect_filesystem(mount_point)
                free, total = _volume_stats(mount_point)
            except (subprocess.CalledProcessError, OSError):
                continue
            out.append(
                Drive(
                    device_id=part["DeviceIdentifier"],
                    volume_name=part.get("VolumeName", "(unnamed)"),
                    mount_point=mount_point,
                    fs=fs,
                    free_bytes=free,
                    total_bytes=total,
                    is_external=True,
                )
            )
    return out


def _human(n: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    f = float(n)
    for u in units:
        if f < 1000:
            return f"{f:.1f} {u}"
        f /= 1000
    return f"{f:.1f} PB"


def _render_table(drives: list[Drive]) -> str:
    rows = ["External drives available:\n"]
    for i, d in enumerate(drives, start=1):
        flag = "  ⚠ will need reformat" if needs_reformat(d.fs) else ""
        name = d.volume_name[:18]
        rows.append(
            f"  [{i}]  {name:<18} {d.fs.upper():<7} "
            f"{_human(d.free_bytes)} free / {_human(d.total_bytes)}   "
            f"{d.mount_point}{flag}"
        )
    return "\n".join(rows)


def pick_drive_interactive(drives: list[Drive]) -> Drive:
    if not drives:
        print("No external drives mounted. Plug one in and try again.", file=sys.stderr)
        raise SystemExit(1)
    print(_render_table(drives))
    prompt = f"\nSelect target drive [1-{len(drives)}], or 'q' to quit: "
    while True:
        choice = input(prompt).strip().lower()
        if choice == "q":
            raise SystemExit(0)
        try:
            idx = int(choice)
        except ValueError:
            print(f"  not a number: {choice!r}")
            continue
        if 1 <= idx <= len(drives):
            return drives[idx - 1]
        print(f"  out of range: {idx}")


def confirm_reformat(drive: Drive, *, yes_erase: bool = False) -> bool:
    """Show the typed-confirmation gate. Return True if user authorizes erase."""
    print(
        f"\nDrive '{drive.volume_name}' ({drive.device_id}) is {drive.fs}.\n"
        "This archive uses hardlinks for multi-album items, which require APFS or HFS+.\n"
    )
    print(f"Reformat {drive.volume_name} as APFS now?")
    if yes_erase:
        print("(--yes-erase set — proceeding without typed-confirmation)\n")
        return True
    print(f"   [type 'ERASE {drive.volume_name}' to confirm]\n")
    print("⚠️  THIS WILL PERMANENTLY DELETE EVERYTHING ON THIS DRIVE.")
    print("⚠️  Other drives are NOT affected.")
    print("⚠️  This action cannot be undone.\n")
    given = input("> ").strip()
    return given == f"ERASE {drive.volume_name}"


def reformat_apfs(drive: Drive) -> None:
    """Reformat `drive.device_id` as APFS named after the existing volume name."""
    print(f"Reformatting {drive.device_id} ('{drive.volume_name}') as APFS...")
    # Output intentionally NOT captured — the user needs to see diskutil's
    # progress on this destructive operation.
    subprocess.run(
        ["diskutil", "eraseDisk", "APFS", drive.volume_name, drive.device_id],
        check=True,
    )


def caffeinate_for_run() -> subprocess.Popen[bytes]:
    """Spawn `caffeinate -dimsu` to prevent sleep. Caller must terminate the Popen."""
    return subprocess.Popen(["caffeinate", "-dimsu"])
