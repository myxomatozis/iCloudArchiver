from pathlib import Path

import pytest

from icloud_archiver.auth import SessionUnavailable, interactive_login, load_session


class _FakePyiCloudService:
    """Minimal stand-in for pyicloud_ipd.PyiCloudService used in tests."""

    def __init__(self, *, requires_2sa: bool = False, verify_ok: bool = True) -> None:
        self.requires_2sa = requires_2sa
        self._verify_ok = verify_ok
        self.trusted_devices = [{"deviceType": "Phone", "phoneNumber": "*** 1234"}]

    def send_verification_code(self, _device: dict[str, str]) -> bool:
        return True

    def validate_verification_code(self, _device: dict[str, str], code: str) -> bool:
        return self._verify_ok and code == "123456"


def test_interactive_login_writes_cookie_and_keychain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cookie_dir = tmp_path / "cookies"
    cookie_dir.mkdir()
    fake_service = _FakePyiCloudService()

    inputs = iter(["test@example.com"])
    monkeypatch.setattr("builtins.input", lambda _p="": next(inputs))
    monkeypatch.setattr("getpass.getpass", lambda _p="": "hunter2")

    constructed: dict[str, object] = {}

    def fake_ctor(email: str, password: str, cookie_directory: str) -> _FakePyiCloudService:
        constructed["email"] = email
        constructed["password"] = password
        constructed["cookie_directory"] = cookie_directory
        return fake_service

    monkeypatch.setattr("icloud_archiver.auth._PyiCloudService", fake_ctor)
    kept_passwords: dict[str, str] = {}
    monkeypatch.setattr(
        "icloud_archiver.auth.keyring.set_password",
        lambda service, user, pw: kept_passwords.__setitem__(f"{service}:{user}", pw),
    )

    interactive_login(cookie_dir)

    assert constructed["email"] == "test@example.com"
    assert constructed["password"] == "hunter2"
    assert constructed["cookie_directory"] == str(cookie_dir)
    assert kept_passwords["icloud-archiver:test@example.com"] == "hunter2"


def test_load_session_returns_service_when_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cookie_dir = tmp_path / "cookies"
    cookie_dir.mkdir()
    (cookie_dir / "test@example.com").write_text("cookie-blob")

    monkeypatch.setattr("icloud_archiver.auth.keyring.get_password", lambda _s, _u: "pw")

    fake = _FakePyiCloudService()
    monkeypatch.setattr(
        "icloud_archiver.auth._PyiCloudService",
        lambda email, password, cookie_directory: fake,
    )

    svc = load_session(cookie_dir, email="test@example.com")
    assert svc is fake


def test_load_session_raises_when_no_keychain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cookie_dir = tmp_path / "cookies"
    cookie_dir.mkdir()
    monkeypatch.setattr("icloud_archiver.auth.keyring.get_password", lambda _s, _u: None)
    with pytest.raises(SessionUnavailable):
        load_session(cookie_dir, email="missing@example.com")
