"""Desktop storage/key defaults must survive upgrades and respect explicit config."""

import os
import stat

import pytest
from cryptography.fernet import Fernet

from camoufox_pm import config

REAL_DESKTOP_DATA_DIR = config.desktop_data_dir


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CPM_DB_PATH", raising=False)
    monkeypatch.delenv("CPM_SECRET_KEY", raising=False)
    monkeypatch.setattr(config, "desktop_data_dir", lambda: tmp_path / "desktop-data")
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


def test_first_desktop_start_creates_a_private_persistent_key(tmp_path):
    config.bootstrap_desktop()
    settings = config.get_settings()
    key_path = tmp_path / "desktop-data" / "secret.key"
    assert settings.db_path == str(tmp_path / "desktop-data" / "profiles.db")
    assert settings.secret_key == key_path.read_text()
    Fernet(settings.secret_key.encode())
    if os.name != "nt":
        assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(key_path.parent.stat().st_mode) == 0o700


def test_restart_reuses_the_same_key_after_the_database_exists(tmp_path, monkeypatch):
    config.bootstrap_desktop()
    first_key = config.get_settings().secret_key
    (tmp_path / "desktop-data" / "profiles.db").write_bytes(b"existing database")
    monkeypatch.delenv("CPM_SECRET_KEY")
    config.bootstrap_desktop()
    assert config.get_settings().secret_key == first_key
    assert (tmp_path / "desktop-data" / "profiles.db").read_bytes() == b"existing database"


def test_existing_database_without_key_is_not_given_a_replacement(tmp_path):
    directory = tmp_path / "desktop-data"
    directory.mkdir()
    (directory / "profiles.db").write_bytes(b"encrypted existing database")
    config.bootstrap_desktop()
    assert config.get_settings().secret_key is None
    assert not (directory / "secret.key").exists()
    assert (directory / "profiles.db").read_bytes() == b"encrypted existing database"


def test_explicit_environment_database_and_secret_win(tmp_path, monkeypatch):
    database = tmp_path / "custom" / "kept.db"
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("CPM_DB_PATH", str(database))
    monkeypatch.setenv("CPM_SECRET_KEY", key)
    config.bootstrap_desktop()
    assert config.get_settings().db_path == str(database)
    assert config.get_settings().secret_key == key
    assert not (database.parent / "secret.key").exists()
    assert not (tmp_path / "desktop-data").exists()


def test_dotenv_database_and_secret_win(tmp_path):
    database = tmp_path / "from-dotenv" / "kept.db"
    key = Fernet.generate_key().decode()
    (tmp_path / ".env").write_text(f"CPM_DB_PATH={database}\nCPM_SECRET_KEY={key}\n")
    config.bootstrap_desktop()
    assert config.get_settings().db_path == str(database)
    assert config.get_settings().secret_key == key
    assert not (tmp_path / "desktop-data").exists()


def test_corrupted_persistent_key_fails_without_overwriting_it(tmp_path):
    directory = tmp_path / "desktop-data"
    directory.mkdir()
    key_path = directory / "secret.key"
    key_path.write_text("invalid saved key")
    with pytest.raises(ValueError):
        config.bootstrap_desktop()
    assert key_path.read_text() == "invalid saved key"


@pytest.mark.parametrize(
    ("platform", "environment", "suffix"),
    [
        ("darwin", {}, "Library/Application Support/Camoufox Profile Manager"),
        (
            "win32",
            {"LOCALAPPDATA": "configured-local"},
            "configured-local/Camoufox Profile Manager",
        ),
        ("linux", {"XDG_DATA_HOME": "configured-xdg"}, "configured-xdg/camoufox-profile-manager"),
    ],
)
def test_platform_data_directory_does_not_depend_on_working_directory(
    tmp_path, monkeypatch, platform, environment, suffix
):
    from pathlib import Path

    monkeypatch.setattr(config.sys, "platform", platform)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    for key, value in environment.items():
        monkeypatch.setenv(key, str(tmp_path / value))
    expected = (tmp_path / "home" / suffix) if platform == "darwin" else tmp_path / suffix
    assert REAL_DESKTOP_DATA_DIR() == expected
