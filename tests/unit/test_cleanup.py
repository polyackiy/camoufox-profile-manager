"""Reconciling profile directories with the database.

This module deletes directories from disk, and a profile directory is the
account: cookies, storage, saved logins. Every test here is about what survives.
"""

import shutil

import pytest

from camoufox_pm.core.cleanup import ProfileCleanupManager


@pytest.fixture
async def cleanup(profile_manager, tmp_path):
    """A cleanup manager pointed at the same storage as ``profile_manager``."""
    manager = ProfileCleanupManager(str(tmp_path), str(tmp_path / "test.db"))
    await manager.initialize()
    yield manager
    await manager.close()


def make_orphan(tmp_path, name: str = "deadbeef", size: int = 2048):
    """Create a profile directory on disk that no database row refers to."""
    directory = tmp_path / "profiles" / f"profile_{name}"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "cookies.sqlite").write_bytes(b"x" * size)
    return directory


@pytest.mark.asyncio
async def test_a_directory_no_profile_refers_to_is_orphaned(cleanup, tmp_path):
    orphan = make_orphan(tmp_path)

    found = await cleanup.find_orphaned_profile_directories()

    assert [item["path"] for item in found] == [orphan]
    assert found[0]["profile_id"] == "deadbeef"
    assert found[0]["size_mb"] == pytest.approx(2048 / (1024 * 1024))


@pytest.mark.asyncio
async def test_a_directory_a_profile_refers_to_is_not_orphaned(cleanup, profile_manager):
    await profile_manager.create_profile(name="live")

    assert await cleanup.find_orphaned_profile_directories() == []


@pytest.mark.asyncio
async def test_cleanup_removes_the_orphan_and_leaves_the_live_profile_alone(
    cleanup, profile_manager, tmp_path
):
    """The whole point of the module, and the one mistake that cannot be undone."""
    profile = await profile_manager.create_profile(name="live")
    live_dir = tmp_path / "profiles" / f"profile_{profile.id}"
    (live_dir / "cookies.sqlite").write_bytes(b"session")
    orphan = make_orphan(tmp_path)

    removed = await cleanup.cleanup_orphaned_directories(
        await cleanup.find_orphaned_profile_directories(), confirm=False
    )

    assert removed == 1
    assert not orphan.exists()
    assert (live_dir / "cookies.sqlite").read_bytes() == b"session"


@pytest.mark.asyncio
async def test_anything_that_is_not_a_profile_directory_is_left_alone(cleanup, tmp_path):
    """The profiles directory is a place users put things: backups, notes, exports.

    Only ``profile_*`` directories are ours to delete.
    """
    profiles = tmp_path / "profiles"
    profiles.mkdir(parents=True, exist_ok=True)
    backup = profiles / "backup-before-upgrade"
    backup.mkdir()
    (backup / "keep.txt").write_text("mine")
    loose_file = profiles / "profile_notes.txt"
    loose_file.write_text("not a directory")

    await cleanup.cleanup_orphaned_directories(
        await cleanup.find_orphaned_profile_directories(), confirm=False
    )

    assert (backup / "keep.txt").exists()
    assert loose_file.exists()


@pytest.mark.parametrize(("answer", "expected"), [("yes", 1), ("y", 1), ("no", 0), ("", 0)])
@pytest.mark.asyncio
async def test_the_prompt_decides_whether_anything_is_deleted(
    cleanup, tmp_path, monkeypatch, answer, expected
):
    orphan = make_orphan(tmp_path)
    monkeypatch.setattr("builtins.input", lambda _: answer)

    removed = await cleanup.cleanup_orphaned_directories(
        await cleanup.find_orphaned_profile_directories(), confirm=True
    )

    assert removed == expected
    assert orphan.exists() is (expected == 0)


@pytest.mark.asyncio
async def test_one_directory_that_cannot_be_removed_does_not_strand_the_rest(
    cleanup, tmp_path, monkeypatch
):
    """A locked or permission-denied directory must not abort the run.

    Half a cleanup that reports failure is worse than a cleanup that skips one
    directory and says how many it managed.
    """
    stubborn = make_orphan(tmp_path, "aaaaaaaa")
    removable = make_orphan(tmp_path, "bbbbbbbb")
    real_rmtree = shutil.rmtree

    def refuse_one(path, *args, **kwargs):
        if path == stubborn:
            raise PermissionError("in use")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", refuse_one)

    removed = await cleanup.cleanup_orphaned_directories(
        await cleanup.find_orphaned_profile_directories(), confirm=False
    )

    assert removed == 1
    assert stubborn.exists()
    assert not removable.exists()


@pytest.mark.asyncio
async def test_a_profile_without_a_directory_is_reported_and_can_be_recreated(
    cleanup, profile_manager, tmp_path
):
    profile = await profile_manager.create_profile(name="lost")
    shutil.rmtree(tmp_path / "profiles" / f"profile_{profile.id}")

    missing = await cleanup.find_missing_profile_directories()
    assert [item["profile"].id for item in missing] == [profile.id]

    assert await cleanup.create_missing_directories(missing) == 1
    assert (tmp_path / "profiles" / f"profile_{profile.id}").is_dir()


@pytest.mark.asyncio
async def test_a_dry_run_changes_nothing_on_disk(cleanup, profile_manager, tmp_path):
    profile = await profile_manager.create_profile(name="lost")
    shutil.rmtree(tmp_path / "profiles" / f"profile_{profile.id}")
    orphan = make_orphan(tmp_path)

    results = await cleanup.auto_cleanup(dry_run=True)

    assert results == {"orphaned_removed": 0, "directories_created": 0}
    assert orphan.exists()
    assert not (tmp_path / "profiles" / f"profile_{profile.id}").exists()


@pytest.mark.asyncio
async def test_auto_cleanup_both_removes_and_recreates(cleanup, profile_manager, tmp_path):
    profile = await profile_manager.create_profile(name="lost")
    shutil.rmtree(tmp_path / "profiles" / f"profile_{profile.id}")
    orphan = make_orphan(tmp_path)

    results = await cleanup.auto_cleanup()

    assert results == {"orphaned_removed": 1, "directories_created": 1}
    assert not orphan.exists()
    assert (tmp_path / "profiles" / f"profile_{profile.id}").is_dir()


@pytest.mark.asyncio
async def test_the_diagnostic_counts_what_is_actually_there(cleanup, profile_manager, tmp_path):
    healthy = await profile_manager.create_profile(name="healthy")
    (tmp_path / "profiles" / f"profile_{healthy.id}" / "places.sqlite").write_bytes(b"y" * 1024)
    lost = await profile_manager.create_profile(name="lost")
    shutil.rmtree(tmp_path / "profiles" / f"profile_{lost.id}")
    make_orphan(tmp_path, size=1024)

    report = await cleanup.full_diagnostic()

    assert report["total_profiles_in_db"] == 2
    assert report["total_directories_on_disk"] == 2  # the healthy one and the orphan
    assert report["orphaned_directories"] == 1
    assert report["missing_directories"] == 1
    assert report["healthy_profiles"] == 1
    assert report["issues_found"] == 2
    assert report["total_disk_size_mb"] == pytest.approx(2048 / (1024 * 1024))


@pytest.mark.asyncio
async def test_a_fresh_install_with_no_profiles_directory_is_not_an_error(tmp_path):
    manager = ProfileCleanupManager(str(tmp_path / "empty"), str(tmp_path / "empty" / "test.db"))
    await manager.initialize()
    try:
        assert manager.get_profile_directories_on_disk() == []
        assert await manager.find_orphaned_profile_directories() == []
        assert await manager.auto_cleanup() == {"orphaned_removed": 0, "directories_created": 0}
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_it_reads_the_database_it_was_given(profile_manager, tmp_path):
    """Regression: the database was assumed to be ``<data_dir>/profiles.db``.

    Any other configured file name (``CPM_DB_PATH=data/mine.db``) meant the
    cleanup opened an empty database beside the real one, found no profiles in
    it, and so considered every directory on disk orphaned.
    """
    profile = await profile_manager.create_profile(name="live")

    manager = ProfileCleanupManager(str(tmp_path), str(tmp_path / "test.db"))
    await manager.initialize()
    try:
        assert await manager.find_orphaned_profile_directories() == []
        assert [p.id for p in await manager.get_profiles_in_database()] == [profile.id]
    finally:
        await manager.close()
