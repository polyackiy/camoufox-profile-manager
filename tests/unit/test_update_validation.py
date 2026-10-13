"""Use the actual upstream config and pin resolver, without launching a browser."""

import json

import pytest

from camoufox_pm.core.update_validation import _activate_paired_browser


@pytest.fixture
def upstream(tmp_path, monkeypatch):
    import camoufox.browser_pin as pin
    import camoufox.multiversion as multi

    root = tmp_path / "browser"
    root.mkdir()
    monkeypatch.setattr(multi, "INSTALL_DIR", root)
    monkeypatch.setattr(multi, "BROWSERS_DIR", root / "browsers")
    monkeypatch.setattr(multi, "CONFIG_FILE", root / "config.json")
    release = pin.BrowserPin("156-beta.2", "upstream/camoufox", "official", "156.0", "beta.2")
    monkeypatch.setattr(pin, "load_pin", lambda: release)
    digest = "a" * 64
    for repo, suffix in [
        ("official", "legacy"),
        ("cpm-verified", digest),
        ("cpm-verified", "b" * 64),
    ]:
        path = root / "browsers" / repo / f"156.0-beta.2-{suffix}"
        path.mkdir(parents=True)
        (path / "version.json").write_text(
            json.dumps(
                {"version": "156.0", "build": "beta.2", "sha256": digest, "prerelease": True}
            )
        )
        if repo == "cpm-verified":
            (path / ".cpm-pair.json").write_text(
                json.dumps({"version": release.spec, "sha256": suffix})
            )
    multi.save_config({"channel": "old/stable", "pinned": "old-version", "pinned_sha": "stale"})
    return multi, pin, release, digest


def test_exact_verified_cache_selection_uses_upstream_pin_protocol(upstream):
    from camoufox.__main__ import _pin_target

    multi, pin, release, digest = upstream
    paired = _activate_paired_browser(
        {"manifest": {"browser_version": release.spec}, "browser_sha256": digest}
    )
    config = multi.load_config()
    assert paired.repo_name == "cpm-verified"
    assert paired.path.name.endswith(digest)
    assert config["channel"] == "official/prerelease"
    assert config["pinned"] == release.spec
    assert config["pinned_sha"] == digest
    assert multi.get_active_path() == paired.path
    target = {"version": release.version, "build": release.build, "sha256": digest}
    assert _pin_target({"versions": [target]}, config["pinned"], config["pinned_sha"]) == target


def test_installed_wrapper_release_pin_must_match_before_config_write(upstream):
    multi, pin, release, digest = upstream
    before = multi.load_config()
    with pytest.raises(ValueError, match="wrapper release pin"):
        _activate_paired_browser(
            {"manifest": {"browser_version": "155.0-beta.1"}, "browser_sha256": digest}
        )
    assert multi.load_config() == before


def test_unstamped_wrapper_clears_stale_digest_and_uses_real_default_origin(upstream, monkeypatch):
    multi, pin, release, digest = upstream
    monkeypatch.setattr(pin, "load_pin", lambda: None)
    # Keep one authenticated cache candidate so the legacy no-digest contract is
    # unambiguous. It cannot retain another version's stale upstream digest.
    candidates = [item for item in multi.list_installed() if item.repo_name == "cpm-verified"]
    monkeypatch.setattr(multi, "list_installed", lambda: [candidates[0]])
    paired = _activate_paired_browser({"manifest": {"browser_version": release.spec}})
    assert multi.load_config()["pinned"] == release.spec
    assert multi.load_config()["channel"] == "official/prerelease"
    assert "pinned_sha" not in multi.load_config()
    assert multi.get_active_path() == paired.path


def test_recorded_pair_path_and_digest_choose_reissued_cache(upstream):
    multi, pin, release, digest = upstream
    path = multi.BROWSERS_DIR / "cpm-verified" / f"{release.spec}-{digest}"
    paired = _activate_paired_browser(
        {
            "manifest": {"browser_version": release.spec},
            "browser_sha256": digest,
            "paired_browser_path": str(path),
        }
    )
    assert paired.path == path
    assert multi.load_config()["pinned_sha"] == digest


def test_recorded_pair_marker_change_refused_before_activation(upstream):
    multi, pin, release, digest = upstream
    path = multi.BROWSERS_DIR / "cpm-verified" / f"{release.spec}-{digest}"
    (path / ".cpm-pair.json").write_text(json.dumps({"version": release.spec, "sha256": "wrong"}))
    before = multi.load_config()
    with pytest.raises(ValueError, match="cache identity"):
        _activate_paired_browser(
            {
                "manifest": {"browser_version": release.spec},
                "browser_sha256": digest,
                "paired_browser_path": str(path),
            }
        )
    assert multi.load_config() == before
