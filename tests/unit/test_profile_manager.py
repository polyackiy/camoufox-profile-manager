"""Cloning, deleting and launching: the operations that move data around.

CRUD over the API is covered in tests/integration; these are the parts of the
manager that touch the filesystem or a profile's identity.
"""

import shutil
from pathlib import Path

import pytest

from camoufox_pm.core import fingerprint_store
from camoufox_pm.core import profile_manager as pm_module

PINNED = {
    "navigator.userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0",
    "navigator.platform": "Win32",
    "navigator.hardwareConcurrency": 12,
    "screen.width": 2560,
    "screen.height": 1440,
    "webGl:renderer": "NVIDIA GeForce RTX 3070",
}


async def pin(profile_manager, profile):
    """Give a profile a pinned machine, as its first launch would."""
    profile.fingerprint = dict(PINNED)
    await profile_manager.storage.update_profile(profile)
    return profile


# -- Cloning --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_clone_carries_the_browser_data_into_a_directory_of_its_own(
    profile_manager, tmp_path
):
    """Cloning exists to reuse a warmed-up profile, which is that directory."""
    source = await profile_manager.create_profile(name="warm")
    source_dir = Path(source.get_storage_path(str(profile_manager.profiles_dir)))
    (source_dir / "cookies.sqlite").write_bytes(b"logged in")

    clone = await profile_manager.clone_profile(source.id, "warm copy")

    assert clone is not None and clone.id != source.id
    clone_dir = Path(clone.get_storage_path(str(profile_manager.profiles_dir)))
    assert clone_dir != source_dir
    assert (clone_dir / "cookies.sqlite").read_bytes() == b"logged in"
    assert (source_dir / "cookies.sqlite").exists()


@pytest.mark.asyncio
async def test_a_clone_is_a_different_machine(profile_manager):
    """Regression: the pinned fingerprint was copied along with everything else.

    The clone then reported the same GPU, screen, core count and noise seeds as
    its source — two profiles that are provably one machine, which is the single
    thing this product exists to prevent.
    """
    source = await pin(profile_manager, await profile_manager.create_profile(name="source"))

    clone = await profile_manager.clone_profile(source.id, "copy")

    assert clone is not None
    assert clone.fingerprint is None
    assert (await profile_manager.get_profile(source.id)).fingerprint == PINNED


@pytest.mark.asyncio
async def test_a_clone_can_be_asked_to_keep_the_machine(profile_manager):
    """Deliberately the same computer — moving a profile between installs, say."""
    source = await pin(profile_manager, await profile_manager.create_profile(name="source"))

    clone = await profile_manager.clone_profile(source.id, "copy", regenerate_fingerprint=False)

    assert clone is not None
    assert clone.fingerprint == PINNED
    assert clone.browser_settings == source.browser_settings


@pytest.mark.asyncio
async def test_cloning_an_unknown_profile_returns_nothing(profile_manager):
    assert await profile_manager.clone_profile("nope", "copy") is None


# -- Deleting -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deleting_a_profile_removes_its_directory(profile_manager):
    profile = await profile_manager.create_profile(name="gone")
    directory = Path(profile.get_storage_path(str(profile_manager.profiles_dir)))
    (directory / "cookies.sqlite").write_bytes(b"session")

    assert await profile_manager.delete_profile(profile.id) is True
    assert not directory.exists()
    assert await profile_manager.get_profile(profile.id) is None


@pytest.mark.asyncio
async def test_the_data_can_be_kept_when_the_profile_is_deleted(profile_manager):
    """The record goes, the directory stays — for a manual backup before a purge."""
    profile = await profile_manager.create_profile(name="gone")
    directory = Path(profile.get_storage_path(str(profile_manager.profiles_dir)))
    (directory / "cookies.sqlite").write_bytes(b"session")

    await profile_manager.delete_profile(profile.id, remove_data=False)

    assert (directory / "cookies.sqlite").read_bytes() == b"session"


@pytest.mark.asyncio
async def test_a_profile_whose_directory_is_already_gone_still_deletes(profile_manager):
    """Otherwise a half-removed profile could never be cleared from the list."""
    profile = await profile_manager.create_profile(name="gone")
    shutil.rmtree(profile.get_storage_path(str(profile_manager.profiles_dir)))

    assert await profile_manager.delete_profile(profile.id) is True


@pytest.mark.asyncio
async def test_deleting_an_unknown_profile_reports_it_rather_than_raising(profile_manager):
    assert await profile_manager.delete_profile("nope") is False


# -- Groups ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deleting_a_group_ungroups_its_profiles_and_keeps_them(profile_manager):
    """A group is a label. Deleting one must never take the profiles with it."""
    group = await profile_manager.create_group("Social")
    first = await profile_manager.create_profile(name="a", group=group["id"])
    second = await profile_manager.create_profile(name="b", group=group["id"])

    assert await profile_manager.delete_group(group["id"]) is True

    remaining = await profile_manager.list_profiles()
    assert {p.id for p in remaining} == {first.id, second.id}
    assert [p.group for p in remaining] == [None, None]


@pytest.mark.asyncio
async def test_deleting_an_unknown_group_reports_it(profile_manager):
    assert await profile_manager.delete_group("nope") is False


@pytest.mark.asyncio
async def test_a_group_reports_how_many_profiles_it_holds(profile_manager):
    group = await profile_manager.create_group("Social", description="socials")
    await profile_manager.create_profile(name="a", group=group["id"])
    await profile_manager.create_profile(name="b")

    assert (await profile_manager.get_group(group["id"]))["profile_count"] == 1
    assert [g["profile_count"] for g in await profile_manager.list_groups()] == [1]


@pytest.mark.asyncio
async def test_bulk_update_counts_only_the_profiles_that_exist(profile_manager):
    first = await profile_manager.create_profile(name="a")
    second = await profile_manager.create_profile(name="b")

    updated = await profile_manager.bulk_update_profiles(
        [first.id, "missing", second.id], {"status": "inactive"}
    )

    assert updated == 2
    assert [p.status for p in await profile_manager.list_profiles()] == ["inactive", "inactive"]


# -- Launching ------------------------------------------------------------------


@pytest.fixture
def launches(profile_manager, monkeypatch):
    """Capture the launch options without starting a browser.

    Fingerprint resolution is stubbed out too: it reads the installed browser,
    which CI does not have and which would make these depend on the machine.
    """
    recorded: list[dict] = []

    class FakeSession:
        process_id = 4242

    async def fake_launch(profile_id, options, on_exit=None):
        recorded.append(options)
        profile_manager.browser_sessions.active_sessions[profile_id] = FakeSession()
        return FakeSession()

    monkeypatch.setattr(profile_manager.browser_sessions, "launch", fake_launch)
    monkeypatch.setattr(pm_module.fingerprint_store, "resolve", lambda *_args, **_kw: {})
    return recorded


@pytest.mark.asyncio
async def test_launching_an_unknown_profile_says_so(profile_manager, launches):
    with pytest.raises(ValueError, match="not found"):
        await profile_manager.launch_browser("nope")


@pytest.mark.asyncio
async def test_a_window_size_is_passed_as_the_pair_camoufox_expects(profile_manager, launches):
    profile = await profile_manager.create_profile(name="p")

    await profile_manager.launch_browser(profile.id, window_size="1600x900")

    assert launches[0]["window"] == (1600, 900)


@pytest.mark.asyncio
async def test_an_unreadable_window_size_is_ignored_rather_than_failing_the_launch(
    profile_manager, launches
):
    """Losing the requested size is a nuisance; refusing to open the browser is not."""
    profile = await profile_manager.create_profile(name="p", browser_settings={"os": "windows"})

    result = await profile_manager.launch_browser(profile.id, window_size="huge")

    assert result["status"] == "launched"
    assert launches[0]["window"] == (
        profile.browser_settings.window_width,
        profile.browser_settings.window_height,
    )


@pytest.mark.asyncio
async def test_a_second_launch_does_not_open_a_second_browser(profile_manager, launches):
    profile = await profile_manager.create_profile(name="p")

    await profile_manager.launch_browser(profile.id)
    again = await profile_manager.launch_browser(profile.id)

    assert again["status"] == "already_running"
    assert len(launches) == 1


@pytest.mark.asyncio
async def test_a_pinned_machine_is_replayed_on_every_launch(profile_manager, launches):
    profile = await pin(profile_manager, await profile_manager.create_profile(name="p"))

    await profile_manager.launch_browser(profile.id)

    assert launches[0]["config"]["webGl:renderer"] == PINNED["webGl:renderer"]
    assert launches[0]["config"]["screen.width"] == 2560


@pytest.mark.asyncio
async def test_the_profiles_own_settings_still_beat_the_pin(profile_manager, launches, monkeypatch):
    """A pin is the hardware; timezone and geolocation follow the proxy, and the
    profile's explicit values have to survive the merge."""
    profile = await profile_manager.create_profile(
        name="p", browser_settings={"timezone": "Asia/Tokyo", "hardware_concurrency": 4}
    )
    await pin(profile_manager, profile)
    monkeypatch.setattr(
        pm_module.fingerprint_store,
        "resolve",
        lambda *_a, **_k: {"navigator.hardwareConcurrency": 12},
    )

    await profile_manager.launch_browser(profile.id)

    assert launches[0]["config"]["timezone"] == "Asia/Tokyo"
    assert launches[0]["config"]["navigator.hardwareConcurrency"] == 4


@pytest.mark.asyncio
async def test_the_first_launch_pins_the_machine_it_resolved(profile_manager, monkeypatch):
    resolved = {"navigator.platform": "Win32", "screen.width": 1920}

    async def fake_launch(profile_id, options, on_exit=None):
        profile_manager.browser_sessions.active_sessions[profile_id] = object()
        return type("S", (), {"process_id": None})()

    monkeypatch.setattr(profile_manager.browser_sessions, "launch", fake_launch)
    monkeypatch.setattr(pm_module.fingerprint_store, "resolve", lambda *_a, **_k: resolved)
    profile = await profile_manager.create_profile(name="p")

    await profile_manager.launch_browser(profile.id)

    stored = await profile_manager.get_profile(profile.id)
    assert stored.fingerprint == resolved
    assert stored.last_used is not None


@pytest.mark.asyncio
async def test_closing_a_browser_that_is_not_running_says_so(profile_manager):
    result = await profile_manager.close_browser("nope")

    assert result["status"] == "not_running"


@pytest.mark.asyncio
async def test_rotating_the_fingerprint_drops_the_pinned_machine(profile_manager):
    profile = await pin(profile_manager, await profile_manager.create_profile(name="p"))

    rotated = await profile_manager.rotate_profile_fingerprint(profile.id)

    assert rotated is not None and rotated.fingerprint is None


@pytest.mark.asyncio
async def test_refreshing_a_browser_version_without_a_pin_says_to_launch_first(profile_manager):
    profile = await profile_manager.create_profile(name="p")

    with pytest.raises(ValueError, match="no pinned machine"):
        await profile_manager.refresh_browser_version(profile.id)


@pytest.mark.asyncio
async def test_refreshing_reads_the_operating_system_from_the_pin(profile_manager, monkeypatch):
    """The OS dropdown can be changed after the machine was pinned; following it
    would put a macOS user agent onto Windows hardware."""
    profile = await profile_manager.create_profile(name="p", browser_settings={"os": "macos"})
    await pin(profile_manager, profile)
    asked: list[str] = []

    def record(options, preset=None):
        asked.append(options["os"])
        return {"navigator.userAgent": "rv:141.0"}

    monkeypatch.setattr(pm_module.fingerprint_store, "resolve", record)

    await profile_manager.refresh_browser_version(profile.id)

    assert asked == ["windows"]
    assert (
        fingerprint_store.browser_major((await profile_manager.get_profile(profile.id)).fingerprint)
        == 141
    )
