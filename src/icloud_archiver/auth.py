"""Apple ID login wrapping pyicloud-ipd, with cookie + keychain persistence."""

import contextlib
import getpass
import os
import stat
from pathlib import Path
from typing import Any

import keyring
from pyicloud_ipd import PyiCloudService as _PyiCloudService  # type: ignore[import-untyped]

_KEYCHAIN_SERVICE = "icloud-archiver"
_MAX_2FA_TRIES = 3


class AuthError(Exception):
    pass


class SessionUnavailable(AuthError):
    """No valid session — caller should ask user to run `login`."""


def _enforce_cookie_perms(cookie_dir: Path) -> None:
    for p in cookie_dir.iterdir():
        with contextlib.suppress(OSError):
            os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)


def interactive_login(cookie_dir: Path) -> None:
    """Prompt for Apple ID / password / 2FA. Persist cookie + keychain on success."""
    cookie_dir.mkdir(parents=True, exist_ok=True)
    email = input("Apple ID email: ").strip()
    password = getpass.getpass("Apple ID password: ")
    service = _PyiCloudService(email, password, cookie_directory=str(cookie_dir))

    if getattr(service, "requires_2sa", False):
        devices = getattr(service, "trusted_devices", [])
        if not devices:
            raise AuthError("2FA required but no trusted devices listed.")
        device = devices[0]
        if not service.send_verification_code(device):
            raise AuthError("Failed to send 2FA code.")
        for attempt in range(_MAX_2FA_TRIES):
            code = input("2FA code (6 digits): ").strip()
            if service.validate_verification_code(device, code):
                break
            print(f"  code rejected ({_MAX_2FA_TRIES - attempt - 1} retries left)")
        else:
            raise AuthError("2FA verification failed.")

    keyring.set_password(_KEYCHAIN_SERVICE, email, password)
    _enforce_cookie_perms(cookie_dir)
    print(f"Logged in as {email}. Session cookies stored in {cookie_dir}.")


def load_session(cookie_dir: Path, *, email: str) -> Any:
    """Re-open a session using the persisted cookie + keychain password."""
    password = keyring.get_password(_KEYCHAIN_SERVICE, email)
    if not password:
        raise SessionUnavailable(f"no keychain entry for {email}; run `login` first")
    _enforce_cookie_perms(cookie_dir)
    return _PyiCloudService(email, password, cookie_directory=str(cookie_dir))
