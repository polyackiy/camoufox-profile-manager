"""Tests for environment-based application settings."""

from camoufox_pm.config import Settings


def test_defaults_are_local_and_safe():
    s = Settings(_env_file=None)
    assert s.host == "127.0.0.1"
    assert s.api_key is None
    assert s.cors_origins == ["http://localhost:3000"]


def test_cors_origins_parsed_from_csv(monkeypatch):
    monkeypatch.setenv("CPM_CORS_ORIGINS", "http://a.com,http://b.com")
    s = Settings(_env_file=None)
    assert s.cors_origins == ["http://a.com", "http://b.com"]


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("CPM_API_KEY", "secret")
    s = Settings(_env_file=None)
    assert s.api_key == "secret"


def test_the_reported_version_matches_the_package_metadata():
    """A release means editing pyproject.toml, and a second hardcoded copy is the
    one that gets forgotten: a wheel built as 0.2.0 reported 0.1.1 through
    /health and in the OpenAPI schema."""
    from importlib.metadata import version

    from camoufox_pm import __version__

    assert __version__ == version("camoufox-profile-manager")
    assert __version__ != "0.0.0+unknown", "the package should be installed for the test run"
