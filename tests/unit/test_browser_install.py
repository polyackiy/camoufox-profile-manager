"""Offline browser setup: inspect before launch, explicit downloads, safe retries."""

import hashlib
import io
import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from pydantic import SecretStr

from camoufox_pm.core import browser_install
from camoufox_pm.core.browser_session import BrowserLaunchError


@pytest.fixture
def upstream_cache(tmp_path, monkeypatch):
    """Every upstream cache path is temporary; imports never start downloads."""
    from camoufox import addons, fpgen_model, geolocation, multiversion, pkgman

    browser = tmp_path / "browser"
    browser.mkdir()
    executable = browser / "browser-executable"
    executable.write_bytes(b"fake browser")
    version = SimpleNamespace(is_supported=lambda: True, full_string="135.0-beta.1")
    monkeypatch.setattr(multiversion, "get_active_path", lambda: browser)
    monkeypatch.setattr(pkgman.Version, "from_path", lambda path: version)
    monkeypatch.setattr(pkgman, "launch_path", lambda path: str(executable))
    monkeypatch.setattr(pkgman, "installed_interface", lambda path: 1)
    monkeypatch.setattr(pkgman, "interface_supported", lambda interface: interface == 1)
    monkeypatch.setattr(fpgen_model, "is_pinned", lambda: True)
    monkeypatch.setattr(fpgen_model, "ensure_fpgen_model", lambda: None)
    monkeypatch.setattr(
        pkgman, "camoufox_path", Mock(side_effect=AssertionError("implicit download"))
    )
    monkeypatch.setattr(addons, "get_addon_path", lambda name: str(tmp_path / "addons" / name))
    monkeypatch.setattr(geolocation, "MMDB_DIR", tmp_path / "mmdb")
    monkeypatch.setattr(
        geolocation,
        "load_geoip_config",
        lambda: {"name": "Example", "urls": {"combined": ["https://unused.invalid"]}},
    )
    monkeypatch.setattr(pkgman, "INSTALL_DIR", tmp_path / "cache")
    monkeypatch.setattr(multiversion, "BROWSERS_DIR", tmp_path / "browsers")
    return SimpleNamespace(
        addons=addons,
        geolocation=geolocation,
        multiversion=multiversion,
        pkgman=pkgman,
        executable=executable,
        version=version,
        root=tmp_path,
    )


def write_resources(cache):
    for addon in cache.addons.DefaultAddons:
        directory = cache.root / "addons" / addon.name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "manifest.json").write_text(json.dumps({"manifest_version": 2}))
    cache.geolocation.MMDB_DIR.mkdir(exist_ok=True)
    (cache.geolocation.MMDB_DIR / "example-combined.mmdb").write_bytes(b"location data")


def test_readiness_inspects_files_without_triggering_download(upstream_cache, monkeypatch):
    cache = upstream_cache
    monkeypatch.setattr(
        cache.geolocation, "download_mmdb", Mock(side_effect=AssertionError("download"))
    )
    monkeypatch.setattr(
        cache.addons, "maybe_download_addons", Mock(side_effect=AssertionError("download"))
    )
    installer = browser_install.BrowserInstaller()
    assert browser_install.installed_browser() == (True, "135.0-beta.1")
    assert installer.status().installed is False  # binary alone is insufficient
    write_resources(cache)
    status = installer.status()
    assert status.installed and status.state == "ready"
    assert status.version == "135.0-beta.1"
    cache.pkgman.camoufox_path.assert_not_called()


def test_missing_or_unsupported_browser_is_not_reported_ready(upstream_cache, monkeypatch):
    cache = upstream_cache
    write_resources(cache)
    cache.executable.unlink()
    assert browser_install.installed_browser()[0] is False
    cache.executable.write_bytes(b"browser")
    monkeypatch.setattr(
        cache.pkgman.Version, "from_path", lambda path: SimpleNamespace(is_supported=lambda: False)
    )
    assert browser_install.installed_browser() == (False, None)


def test_invalid_addon_manifest_blocks_launch_without_fetching(upstream_cache):
    cache = upstream_cache
    write_resources(cache)
    addon = next(iter(cache.addons.DefaultAddons))
    (cache.root / "addons" / addon.name / "manifest.json").write_text("broken json")
    assert not browser_install.browser_resources_ready()
    with pytest.raises(BrowserLaunchError, match="Settings"):
        browser_install.ensure_browser_ready()
    cache.pkgman.camoufox_path.assert_not_called()


def test_complete_resources_allow_launch_preflight(upstream_cache):
    write_resources(upstream_cache)
    browser_install.ensure_browser_ready()


def test_missing_location_database_blocks_launch(upstream_cache):
    write_resources(upstream_cache)
    (upstream_cache.geolocation.MMDB_DIR / "example-combined.mmdb").unlink()
    with pytest.raises(BrowserLaunchError):
        browser_install.ensure_browser_ready()


def test_missing_fpgen_model_blocks_launch_without_downloading(upstream_cache, monkeypatch):
    from camoufox import fpgen_model

    write_resources(upstream_cache)
    monkeypatch.setattr(fpgen_model, "is_pinned", lambda: False)
    download = Mock(side_effect=AssertionError("implicit model download"))
    monkeypatch.setattr(fpgen_model, "ensure_fpgen_model", download)
    with pytest.raises(BrowserLaunchError, match="Settings"):
        browser_install.ensure_browser_ready()
    download.assert_not_called()


def test_incompatible_browser_interface_is_not_ready(upstream_cache, monkeypatch):
    write_resources(upstream_cache)
    monkeypatch.setattr(upstream_cache.pkgman, "installed_interface", lambda path: 999)
    assert browser_install.installed_browser() == (False, None)


def test_explicit_setup_prepares_the_pinned_model(upstream_cache, monkeypatch):
    from camoufox import fpgen_model

    write_resources(upstream_cache)
    model = Mock()
    monkeypatch.setattr(fpgen_model, "ensure_fpgen_model", model)
    monkeypatch.setattr(upstream_cache.addons, "maybe_download_addons", lambda addons: None)
    browser_install.BrowserInstaller()._install()
    model.assert_called_once_with()


def test_repeated_install_clicks_share_one_background_job(upstream_cache, monkeypatch):
    threads = []

    class FakeThread:
        def __init__(self, **kwargs):
            self.target = kwargs["target"]
            threads.append(self)

        def start(self):
            pass  # No real work until the explicit target is exercised below.

    monkeypatch.setattr(browser_install.threading, "Thread", FakeThread)
    installer = browser_install.BrowserInstaller()
    install = Mock()
    monkeypatch.setattr(installer, "_install", install)
    assert installer.start().state == "downloading"
    assert installer.start().state == "downloading"
    assert len(threads) == 1
    install.assert_not_called()
    assert threads[0].target == installer._run


def test_install_failure_is_visible_and_can_be_retried(upstream_cache, monkeypatch):
    installer = browser_install.BrowserInstaller()
    monkeypatch.setattr(browser_install, "installation_lock", nullcontext)
    monkeypatch.setattr(installer, "_install", Mock(side_effect=OSError("disk full")))
    installer._run()
    failed = installer.status()
    assert failed.state == "error" and "disk full" in failed.error
    assert failed.progress is None
    write_resources(upstream_cache)
    monkeypatch.setattr(installer, "_install", lambda: None)
    installer._run()
    ready = installer.status()
    assert ready.installed and ready.state == "ready"
    assert ready.progress == 100 and ready.error is None


def test_swallowed_upstream_addon_failure_is_not_reported_as_success(upstream_cache, monkeypatch):
    cache = upstream_cache
    monkeypatch.setattr(cache.geolocation, "download_mmdb", lambda: None)
    monkeypatch.setattr(cache.addons, "maybe_download_addons", lambda addons: None)
    installer = browser_install.BrowserInstaller()
    monkeypatch.setattr(browser_install, "installation_lock", nullcontext)
    installer._run()
    assert installer.status().state == "error"
    assert "could not be downloaded" in installer.status().error


@pytest.mark.parametrize("manifest", [None, "broken json"])
def test_incomplete_addon_cache_is_preserved_then_retried(upstream_cache, monkeypatch, manifest):
    cache = upstream_cache
    addon = next(iter(cache.addons.DefaultAddons))
    incomplete = cache.root / "addons" / addon.name
    incomplete.mkdir(parents=True)
    (incomplete / "partial-file").write_text("diagnostic")
    if manifest is not None:
        (incomplete / "manifest.json").write_text(manifest)
    monkeypatch.setattr(cache.geolocation, "download_mmdb", lambda: None)
    monkeypatch.setattr(
        cache.addons, "maybe_download_addons", lambda addons: write_resources(cache)
    )
    browser_install.BrowserInstaller()._install()
    assert (
        incomplete.with_name(incomplete.name + ".incomplete") / "partial-file"
    ).read_text() == "diagnostic"
    assert browser_install.browser_resources_ready()


def fake_fetcher(cache, monkeypatch, *, checksum=None, payload=b"downloaded archive"):
    """Exercise our real streaming/hash adapter inside an upstream installer stub."""
    result = {}

    def initialize(fetcher):
        fetcher.github_repo = "repo/test"
        fetcher.installed_sha256 = checksum
        fetcher._version_obj = SimpleNamespace(
            version="135.0", build="beta.1", full_string="135.0-beta.1"
        )
        fetcher._url = "https://unused.invalid/browser.zip"
        fetcher._selected_version = None
        fetcher.is_prerelease = False

    def install(fetcher, replace=False):
        result["replace"] = replace
        output = io.BytesIO()
        fetcher.download_file(output, fetcher._url)
        result["download"] = output.read()

    class Response:
        headers = {"content-length": str(len(payload))}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def raise_for_status(self):
            pass

        def iter_bytes(self):
            yield payload[:5]
            yield payload[5:]

    monkeypatch.setattr(cache.pkgman.CamoufoxFetcher, "__init__", initialize)
    monkeypatch.setattr(cache.pkgman.CamoufoxFetcher, "install", install)
    monkeypatch.setattr(cache.multiversion, "get_repo_name", lambda repo: "test")
    monkeypatch.setattr(cache.multiversion, "version_folder_name", lambda *args: "target")
    monkeypatch.setattr(browser_install.httpx, "stream", lambda *args, **kwargs: Response())
    monkeypatch.setattr(browser_install, "installed_browser", lambda: (False, None))
    monkeypatch.setattr(browser_install, "browser_resources_ready", lambda: True)
    monkeypatch.setattr(cache.addons, "maybe_download_addons", lambda addons: None)
    return result


def test_browser_download_checks_hash_before_unpacking(upstream_cache, monkeypatch):
    payload = b"downloaded archive"
    result = fake_fetcher(
        upstream_cache, monkeypatch, checksum=hashlib.sha256(payload).hexdigest(), payload=payload
    )
    installer = browser_install.BrowserInstaller()
    installer._install()
    assert result["download"] == payload
    assert result["replace"] is False
    assert installer.status().progress == 97


def test_hash_mismatch_fails_before_installer_accepts_archive(upstream_cache, monkeypatch):
    result = fake_fetcher(upstream_cache, monkeypatch, checksum="0" * 64)
    with pytest.raises(RuntimeError, match="checksum"):
        browser_install.BrowserInstaller()._install()
    assert "download" not in result


def test_retry_replaces_only_target_with_missing_executable(upstream_cache, monkeypatch):
    cache = upstream_cache
    result = fake_fetcher(cache, monkeypatch)
    target = cache.multiversion.BROWSERS_DIR / "test" / "target"
    target.mkdir(parents=True)
    (target / "version.json").write_text("{}")
    monkeypatch.setattr(
        cache.pkgman, "launch_path", Mock(side_effect=FileNotFoundError("missing binary"))
    )
    browser_install.BrowserInstaller()._install()
    assert result["replace"] is True
    assert (target / "version.json").exists()  # Our code delegates replacement to upstream.


def github_transport(monkeypatch, handler):
    client = httpx.Client
    monkeypatch.setattr(
        browser_install.httpx,
        "Client",
        lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs),
    )


def test_github_limit_can_be_retried_with_a_one_attempt_token(monkeypatch):
    from camoufox import pkgman

    monkeypatch.setattr(pkgman, "GITHUB_TOKEN", None)
    credential = "github_pat_test_only"
    authorization = []

    def handler(request):
        assert request.url.host == "api.github.com"
        authorization.append(request.headers.get("authorization"))
        if not request.headers.get("authorization"):
            return httpx.Response(403, headers={"x-ratelimit-remaining": "0"})
        return httpx.Response(200, json=[{"tag_name": "paired-browser"}])

    github_transport(monkeypatch, handler)
    with pytest.raises(RuntimeError, match="limit"):
        browser_install.github_releases("owner/browser")
    assert browser_install.github_releases("owner/browser", SecretStr(credential)) == [
        {"tag_name": "paired-browser"}
    ]
    with pytest.raises(RuntimeError, match="limit"):
        browser_install.github_releases("owner/browser")
    assert authorization == [None, f"Bearer {credential}", None]
    assert pkgman.GITHUB_TOKEN is None


def test_source_environment_token_still_authenticates_release_discovery(monkeypatch):
    from camoufox import pkgman

    monkeypatch.setattr(pkgman, "GITHUB_TOKEN", "environment_test_token")

    def handler(request):
        assert request.headers["authorization"] == "Bearer environment_test_token"
        return httpx.Response(200, json=[])

    github_transport(monkeypatch, handler)
    assert browser_install.github_releases("owner/browser") == []


@pytest.mark.parametrize(
    ("status", "headers", "body", "expected"),
    [
        (401, {}, {}, browser_install.GITHUB_TOKEN_REJECTED),
        (403, {}, {}, browser_install.GITHUB_FORBIDDEN),
        (403, {}, {"message": "API rate limit exceeded"}, browser_install.GITHUB_RATE_LIMIT),
        (403, {"retry-after": "60"}, {}, browser_install.GITHUB_RATE_LIMIT),
        (429, {}, {}, browser_install.GITHUB_RATE_LIMIT),
        (500, {}, {}, browser_install.GITHUB_UNAVAILABLE),
        (200, {}, {"unexpected": True}, browser_install.GITHUB_UNAVAILABLE),
    ],
)
def test_release_errors_have_fixed_messages_without_private_response_data(
    monkeypatch, status, headers, body, expected
):
    credential = "github_pat_private_test_value"
    github_transport(
        monkeypatch,
        lambda _: httpx.Response(status, headers=headers, json={**body, "private": credential}),
    )
    with pytest.raises(RuntimeError) as error:
        browser_install.github_releases("owner/browser", SecretStr(credential))
    assert str(error.value) == expected
    assert credential not in str(error.value)


@pytest.mark.parametrize(
    "redirect",
    [
        "https://untrusted.invalid/releases",
        "http://api.github.com/releases",
        "https://api.github.com:444/releases",
    ],
)
def test_github_token_is_not_forwarded_to_another_origin(monkeypatch, redirect):
    calls = []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(302, headers={"location": redirect})

    github_transport(monkeypatch, handler)
    with pytest.raises(RuntimeError, match="GitHub"):
        browser_install.github_releases("owner/browser", SecretStr("test_token"))
    assert len(calls) == 1


def test_repository_rename_redirect_keeps_authentication(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(301, headers={"location": "/repositories/123/releases"})
        assert request.headers["authorization"] == "Bearer test_token"
        return httpx.Response(200, json=[])

    github_transport(monkeypatch, handler)
    assert browser_install.github_releases("owner/browser", SecretStr("test_token")) == []
    assert len(calls) == 2


def test_authenticated_install_keeps_token_out_of_error_status(upstream_cache, monkeypatch):
    installer = browser_install.BrowserInstaller()
    token = SecretStr("github_pat_private_test_value")
    monkeypatch.setattr(browser_install, "installation_lock", nullcontext)
    install = Mock(side_effect=RuntimeError(token.get_secret_value()))
    monkeypatch.setattr(installer, "_install", install)
    installer._run(token)
    install.assert_called_once_with(token)
    status = installer.status()
    assert status.state == "error" and status.error == browser_install.SETUP_FAILED
    assert token.get_secret_value() not in status.model_dump_json()


def test_progress_fetcher_uses_per_attempt_auth_only_for_release_discovery(
    upstream_cache, monkeypatch
):
    cache = upstream_cache
    result = fake_fetcher(cache, monkeypatch)
    original_initialize = cache.pkgman.CamoufoxFetcher.__init__
    token = SecretStr("github_pat_private_test_value")
    lookup = Mock(return_value=[])
    monkeypatch.setattr(browser_install, "github_releases", lookup)

    def initialize(fetcher):
        original_initialize(fetcher)
        fetcher._get_releases("owner/browser")

    monkeypatch.setattr(cache.pkgman.CamoufoxFetcher, "__init__", initialize)
    stream = browser_install.httpx.stream
    requests = []

    def download(*args, **kwargs):
        requests.append(kwargs)
        return stream(*args, **kwargs)

    monkeypatch.setattr(browser_install.httpx, "stream", download)
    browser_install.BrowserInstaller()._install(token)
    lookup.assert_called_once_with("owner/browser", token)
    assert result["download"] == b"downloaded archive"
    assert requests and all("headers" not in request for request in requests)


def test_authenticated_real_fetcher_keeps_wrapper_browser_pin_and_checksum(
    upstream_cache, monkeypatch
):
    from camoufox.browser_pin import load_pin
    from camoufox.pkgman import RepoConfig

    cache = upstream_cache
    pin = load_pin()
    assert pin is not None
    config = RepoConfig.get_default()
    platform = f"{config.get_os_name()}.{config.get_arch()}"
    paired_url = "https://github.com/example/browser/releases/download/paired/browser.zip"
    digest = "a" * 64
    releases = [
        {
            "tag_name": pin.tag,
            "prerelease": False,
            "assets": [
                {
                    "name": f"camoufox-999.0-beta.1-{platform}.zip",
                    "browser_download_url": "https://unused.invalid/newer",
                },
                {
                    "name": f"camoufox-{pin.version}-{pin.build}-{platform}.zip",
                    "browser_download_url": paired_url,
                    "digest": f"sha256:{digest}",
                },
            ],
        }
    ]

    def handler(request):
        assert request.url.host == "api.github.com"
        assert request.headers["authorization"] == "Bearer test_token"
        return httpx.Response(200, json=releases)

    github_transport(monkeypatch, handler)
    selected = []

    def install(fetcher, replace=False):
        selected.append((fetcher.version, fetcher.build, fetcher._url, fetcher.installed_sha256))

    monkeypatch.setattr(cache.pkgman.CamoufoxFetcher, "install", install)
    monkeypatch.setattr(browser_install, "installed_browser", lambda: (False, None))
    monkeypatch.setattr(browser_install, "browser_resources_ready", lambda: True)
    monkeypatch.setattr(cache.addons, "maybe_download_addons", lambda addons: None)
    browser_install.BrowserInstaller()._install(SecretStr("test_token"))
    assert selected == [(pin.version, pin.build, paired_url, digest)]
