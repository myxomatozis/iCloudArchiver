"""Apple ID login wrapping pyicloud, with cookie + keychain persistence."""

import contextlib
import getpass
import os
import stat
from pathlib import Path
from typing import Any

import keyring
from pyicloud import PyiCloudService as _PyiCloudService  # type: ignore[import-untyped]

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


def interactive_login(cookie_dir: Path) -> str:
    """Prompt for Apple ID / password / 2FA. Persist cookie + keychain on success.

    Returns the authenticated Apple ID email so the caller can record it without
    re-prompting.
    """
    cookie_dir.mkdir(parents=True, exist_ok=True)
    email = input("Apple ID email: ").strip()
    password = getpass.getpass("Apple ID password: ")
    service = _PyiCloudService(email, password, cookie_directory=str(cookie_dir))

    # Modern HSA2: a 6-digit code is pushed to the user's trusted Apple device / SMS.
    if getattr(service, "requires_2fa", False):
        delivered = service.request_2fa_code()
        if not delivered:
            raise AuthError(
                "2FA is required but Apple could not deliver a code automatically "
                "(security key accounts are not supported). "
                "Try logging in via iCloud.com first."
            )
        method = getattr(service, "two_factor_delivery_method", "unknown")
        notice = getattr(service, "two_factor_delivery_notice", None)
        if method == "trusted_device":
            print("A verification code has been sent to your trusted Apple device(s).")
        elif method == "sms":
            print("A verification code has been sent via SMS to your trusted phone number.")
        else:
            print("A verification code has been sent.")
        if notice:
            print(f"  Note: {notice}")
        for attempt in range(_MAX_2FA_TRIES):
            code = input("Two-factor code: ").strip()
            if service.validate_2fa_code(code):
                break
            print(f"  code rejected ({_MAX_2FA_TRIES - attempt - 1} retries left)")
        else:
            raise AuthError("2FA verification failed.")

    # Legacy HSA1: must pick a trusted device and request a code be sent there.
    elif getattr(service, "requires_2sa", False):
        devices = getattr(service, "trusted_devices", [])
        if not devices:
            raise AuthError("2SA required but no trusted devices listed.")
        device = devices[0]
        if not service.send_verification_code(device):
            raise AuthError("Failed to send 2SA code.")
        for attempt in range(_MAX_2FA_TRIES):
            code = input("2SA code (6 digits): ").strip()
            if service.validate_verification_code(device, code):
                break
            print(f"  code rejected ({_MAX_2FA_TRIES - attempt - 1} retries left)")
        else:
            raise AuthError("2SA verification failed.")

    keyring.set_password(_KEYCHAIN_SERVICE, email, password)
    _enforce_cookie_perms(cookie_dir)
    print(f"Logged in as {email}. Session cookies stored in {cookie_dir}.")
    return email


def load_session(cookie_dir: Path, *, email: str) -> Any:
    """Re-open a session using the persisted cookie + keychain password."""
    password = keyring.get_password(_KEYCHAIN_SERVICE, email)
    if not password:
        raise SessionUnavailable(f"no keychain entry for {email}; run `login` first")
    _enforce_cookie_perms(cookie_dir)
    return _PyiCloudService(email, password, cookie_directory=str(cookie_dir))
