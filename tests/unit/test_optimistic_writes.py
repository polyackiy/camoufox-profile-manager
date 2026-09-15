"""Two editors of one profile: the second save is refused, not silently applied.

The lease (tests/unit/test_leases.py) answers who may *run* a profile. This
answers who may *save* it — a different question, because a profile is usually
edited while nobody is running it and there is no lease to consult.
"""

from datetime import datetime

import pytest

from camoufox_pm.core.database import StaleWriteError, StorageManager
from camoufox_pm.core.models import BrowserSettings, Profile


async def _profile(storage: StorageManager, name: str = "p") -> Profile:
    profile = Profile(id=f"pf-{name}", name=name, browser_settings=BrowserSettings())
    await storage.save_profile(profile)
    return profile


# -- The counter ------------------------------------------------------------------


async def test_a_new_profile_starts_at_version_zero(storage):
    profile = await _profile(storage)

    assert (await storage.get_profile(profile.id)).row_version == 0


async def test_a_versioned_save_bumps_the_version(storage):
    profile = await _profile(storage)

    profile.name = "renamed"
    await storage.update_profile(profile, expected_row_version=0)

    assert (await storage.get_profile(profile.id)).row_version == 1
    # The caller's own copy moves with the row, so the next edit has the version
    # this write produced rather than the one it started from.
    assert profile.row_version == 1


async def test_an_unversioned_save_leaves_the_version_alone(storage):
    """Creates, imports and clones write without a version and must not reset it."""
    profile = await _profile(storage)
    await storage.update_profile(profile, expected_row_version=0)

    profile.notes = "written without a version"
    await storage.save_profile(profile)

    assert (await storage.get_profile(profile.id)).row_version == 1


# -- The conflict -----------------------------------------------------------------


async def test_the_second_of_two_editors_is_refused(storage):
    """The bug this exists for: both read version 0, both save, one is lost."""
    profile = await _profile(storage)
    first = await storage.get_profile(profile.id)
    second = await storage.get_profile(profile.id)

    first.name = "renamed by the first editor"
    await storage.update_profile(first, expected_row_version=first.row_version)

    second.notes = "written by the second editor"
    with pytest.raises(StaleWriteError):
        await storage.update_profile(second, expected_row_version=second.row_version)

    # The first editor's change stands, whole and unreverted.
    stored = await storage.get_profile(profile.id)
    assert stored.name == "renamed by the first editor"
    assert stored.notes is None


async def test_a_refused_save_writes_nothing_at_all(storage):
    profile = await _profile(storage)
    stale = await storage.get_profile(profile.id)
    await storage.update_profile(profile, expected_row_version=0)

    stale.name = "should not land"
    stale.notes = "nor should this"
    with pytest.raises(StaleWriteError):
        await storage.update_profile(stale, expected_row_version=stale.row_version)

    stored = await storage.get_profile(profile.id)
    assert stored.name == "p"
    assert stored.notes is None


async def test_the_conflict_names_the_profile_and_the_version_it_expected(storage):
    profile = await _profile(storage)
    await storage.update_profile(profile, expected_row_version=0)

    with pytest.raises(StaleWriteError) as raised:
        await storage.update_profile(profile, expected_row_version=0)

    assert raised.value.profile_id == profile.id
    assert raised.value.expected_row_version == 0
    assert "changed by someone else" in str(raised.value)


async def test_a_versioned_save_never_creates_a_row(storage):
    """Editing a profile someone deleted must not resurrect it."""
    gone = Profile(id="deleted", name="gone", browser_settings=BrowserSettings())

    with pytest.raises(StaleWriteError):
        await storage.update_profile(gone, expected_row_version=0)

    assert await storage.get_profile("deleted") is None


async def test_retrying_against_the_current_version_succeeds(storage):
    """A conflict is a stop, not a dead end: reload, reapply, save."""
    profile = await _profile(storage)
    await storage.update_profile(profile, expected_row_version=0)

    reloaded = await storage.get_profile(profile.id)
    reloaded.notes = "applied after reloading"
    await storage.update_profile(reloaded, expected_row_version=reloaded.row_version)

    assert (await storage.get_profile(profile.id)).notes == "applied after reloading"


# -- Writes that deliberately stay outside the check -------------------------------


async def test_a_proxy_check_does_not_make_a_concurrent_edit_go_stale(storage):
    """#43's reasoning, now that there is a version it could have bumped.

    A check can take thirty seconds against a proxy that never answers. If it
    bumped the version, every form open during that wait would be refused on
    save — turning a mechanism against lost updates into a source of spurious
    conflicts. The same reasoning covers set_launch_pin.
    """
    profile = await _profile(storage)
    editor = await storage.get_profile(profile.id)

    await storage.set_proxy_check(profile.id, None)

    editor.name = "edited while the proxy was being checked"
    await storage.update_profile(editor, expected_row_version=editor.row_version)
    assert (await storage.get_profile(profile.id)).name == editor.name


async def test_a_launch_does_not_make_a_concurrent_edit_go_stale(storage):
    """Starting a browser is not an edit of the profile."""
    profile = await _profile(storage)
    editor = await storage.get_profile(profile.id)

    await storage.set_launch_pin(profile.id, {"navigator.userAgent": "x"}, datetime.now())

    editor.notes = "edited while a browser was starting"
    await storage.update_profile(editor, expected_row_version=editor.row_version)

    assert (await storage.get_profile(profile.id)).notes == "edited while a browser was starting"


async def test_a_stale_in_memory_profile_still_reverts_a_targeted_write(storage):
    """The boundary of the version check, asserted rather than assumed.

    A targeted write deliberately does not bump the version, so it cannot make
    an open edit form go stale. The price is that it also cannot stop a whole-row
    save built on a Profile read before it. That is why every writer in
    ProfileManager re-reads the row and applies fields to *that* copy, instead of
    writing back a Profile it was handed — see the manager-level test in
    tests/unit/test_profile_manager.py for the path users actually take.
    """
    profile = await _profile(storage)
    read_before_the_launch = await storage.get_profile(profile.id)

    await storage.set_launch_pin(profile.id, {"navigator.userAgent": "x"}, datetime.now())

    read_before_the_launch.notes = "saved from a copy that predates the pin"
    await storage.update_profile(
        read_before_the_launch, expected_row_version=read_before_the_launch.row_version
    )

    assert (await storage.get_profile(profile.id)).fingerprint is None


async def test_a_launch_pin_does_not_touch_the_lease(storage):
    profile = await _profile(storage)
    await storage.acquire_lease(profile.id, "holder:1:abc", 120)

    await storage.set_launch_pin(profile.id, {"a": 1}, datetime.now())

    assert (await storage.get_lease(profile.id))[0] == "holder:1:abc"


# -- Through the manager, which is the path a request takes ------------------------


async def test_the_manager_refuses_an_edit_built_on_an_old_version(profile_manager):
    profile = await profile_manager.create_profile(name="p")
    stale_version = profile.row_version
    await profile_manager.update_profile(profile.id, {"name": "renamed by the first editor"})

    with pytest.raises(StaleWriteError):
        await profile_manager.update_profile(
            profile.id, {"notes": "from a stale form"}, expected_row_version=stale_version
        )

    stored = await profile_manager.get_profile(profile.id)
    assert stored.name == "renamed by the first editor"
    assert stored.notes is None


async def test_the_manager_accepts_an_edit_built_on_the_current_version(profile_manager):
    profile = await profile_manager.create_profile(name="p")
    current = (await profile_manager.get_profile(profile.id)).row_version

    updated = await profile_manager.update_profile(
        profile.id, {"notes": "applied"}, expected_row_version=current
    )

    assert updated.notes == "applied"
    assert updated.row_version == current + 1


async def test_an_edit_without_a_version_still_works(profile_manager):
    """Back-compatible: a client written before this existed keeps working."""
    profile = await profile_manager.create_profile(name="p")

    updated = await profile_manager.update_profile(profile.id, {"notes": "no version sent"})

    assert updated.notes == "no version sent"


async def test_an_edit_after_a_launch_keeps_the_pinned_machine(profile_manager, monkeypatch):
    """The path users actually take: the manager re-reads, so nothing is reverted."""
    from camoufox_pm.core import profile_manager as pm_module

    class FakeSession:
        process_id = 1

        async def terminate(self):
            pass

    async def fake_launch(profile_id, options, on_exit=None):
        profile_manager.browser_sessions.active_sessions[profile_id] = FakeSession()
        return FakeSession()

    monkeypatch.setattr(profile_manager.browser_sessions, "launch", fake_launch)
    monkeypatch.setattr(
        pm_module.fingerprint_store, "resolve", lambda *_a, **_kw: {"navigator.userAgent": "pinned"}
    )

    profile = await profile_manager.create_profile(name="p")
    await profile_manager.launch_browser(profile.id)

    await profile_manager.update_profile(profile.id, {"notes": "edited after the launch"})

    stored = await profile_manager.get_profile(profile.id)
    assert stored.fingerprint == {"navigator.userAgent": "pinned"}
    assert stored.notes == "edited after the launch"
    assert stored.last_used is not None


async def test_an_edit_made_while_a_launch_resolves_is_not_reverted(profile_manager, monkeypatch):
    """The hazard the launch path used to carry, reproduced.

    resolve() can go to the network, and a launch used to write the whole
    Profile back afterwards — reverting anything saved during that wait. Here
    the rename lands while resolve is running, exactly as another instance's
    save would.
    """
    from camoufox_pm.core import profile_manager as pm_module

    class FakeSession:
        process_id = 1

        async def terminate(self):
            pass

    async def fake_launch(profile_id, options, on_exit=None):
        profile_manager.browser_sessions.active_sessions[profile_id] = FakeSession()
        return FakeSession()

    profile = await profile_manager.create_profile(name="before the launch")

    def resolve_slowly(*_args, **_kwargs):
        # Another instance saving mid-resolve. Written through the connection
        # because this runs synchronously inside the launch.
        profile_manager.storage.db._connection.execute(  # noqa: SLF001
            "UPDATE profiles SET name = ? WHERE id = ?", ("renamed during the launch", profile.id)
        )
        profile_manager.storage.db._connection.commit()  # noqa: SLF001
        return {"navigator.userAgent": "pinned"}

    monkeypatch.setattr(profile_manager.browser_sessions, "launch", fake_launch)
    monkeypatch.setattr(pm_module.fingerprint_store, "resolve", resolve_slowly)

    await profile_manager.launch_browser(profile.id)

    stored = await profile_manager.get_profile(profile.id)
    assert stored.name == "renamed during the launch"
    assert stored.fingerprint == {"navigator.userAgent": "pinned"}
