from pathlib import Path

import pytest

from icloud_archiver.config import parse_size, state_dir


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1", 1),
        ("1024", 1024),
        ("1KB", 1_000),
        ("1MB", 1_000_000),
        ("1GB", 1_000_000_000),
        ("1TB", 1_000_000_000_000),
        ("500GB", 500_000_000_000),
        ("1.5TB", 1_500_000_000_000),
        ("1 TB", 1_000_000_000_000),
        ("  1tb  ", 1_000_000_000_000),
        ("1KiB", 1024),
        ("1MiB", 1024 * 1024),
        ("1GiB", 1024**3),
        ("1TiB", 1024**4),
        ("1B", 1),
        ("1b", 1),
        ("0", 0),
        ("0B", 0),
        ("1.0KB", 1_000),
    ],
)
def test_parse_size_valid(raw: str, expected: int) -> None:
    assert parse_size(raw) == expected


@pytest.mark.parametrize("raw", ["", "abc", "1XB", "-1GB", "1.5.6GB"])
def test_parse_size_invalid(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_size(raw)


def test_state_dir_resolves_to_dot_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    sd = state_dir()
    assert sd == tmp_path / ".icloud-archiver"
    assert sd.is_dir()  # created if missing
    assert (sd / "cookies").is_dir()
    assert (sd / "logs").is_dir()
    assert (sd / "plans").is_dir()
