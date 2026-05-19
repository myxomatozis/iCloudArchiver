"""Configuration helpers: human-size parsing, state directory paths."""

import re
from pathlib import Path

_DECIMAL_UNITS = {
    "": 1,
    "B": 1,
    "KB": 1_000,
    "MB": 1_000_000,
    "GB": 1_000_000_000,
    "TB": 1_000_000_000_000,
}
_BINARY_UNITS = {"KIB": 1024, "MIB": 1024**2, "GIB": 1024**3, "TIB": 1024**4}
_ALL_UNITS = {**_DECIMAL_UNITS, **_BINARY_UNITS}

_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([A-Za-z]*)\s*$")


def parse_size(raw: str) -> int:
    """Parse a human-readable size like '1TB', '500GB', '1.5TiB' to a byte count.

    Accepted forms:
      - Bare integer or decimal number, treated as bytes ("1024" → 1024).
      - `B` suffix for bytes ("1B" → 1). Case-insensitive.
      - Decimal SI units `KB`/`MB`/`GB`/`TB` (powers of 1000). Case-insensitive.
      - Binary IEC units `KiB`/`MiB`/`GiB`/`TiB` (powers of 1024). Case-insensitive.
      - Surrounding whitespace tolerated.

    Fractional inputs are truncated toward zero after multiplication (this
    matches the "threshold must not be exceeded" semantics used by the CLI).
    """
    if not raw or not raw.strip():
        raise ValueError(f"empty size string: {raw!r}")
    m = _SIZE_RE.match(raw)
    if not m:
        raise ValueError(f"could not parse size: {raw!r}")
    number_str, unit = m.group(1), m.group(2).upper()
    if unit not in _ALL_UNITS:
        raise ValueError(f"unknown size unit: {unit!r}")
    value = float(number_str) * _ALL_UNITS[unit]
    return int(value)


def state_dir() -> Path:
    """Return the per-user state directory, ensuring the standard subdirs exist."""
    base = Path.home() / ".icloud-archiver"
    base.mkdir(parents=True, exist_ok=True)
    (base / "cookies").mkdir(exist_ok=True)
    (base / "logs").mkdir(exist_ok=True)
    (base / "plans").mkdir(exist_ok=True)
    return base
