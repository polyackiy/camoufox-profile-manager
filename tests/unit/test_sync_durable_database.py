"""Journal completion may only follow a durable version-checked pointer commit."""

import pytest

from camoufox_pm.core.database import StaleWriteError
from camoufox_pm.core.models import Profile


@pytest.mark.asyncio
async def test_durable_pointer_cas_uses_full_and_preserves_lease(storage, monkeypatch):
    profile = Profile(name="local")
    await storage.save_profile(profile)
    await storage.acquire_lease(profile.id, "test-holder", 300)
    before = await storage.get_profile(profile.id)
    original = storage.db._save_profile_if_unchanged
    modes = []

    def observed(candidate, version):
        modes.append(storage.db._connection.execute("PRAGMA synchronous").fetchone()[0])
        return original(candidate, version)

    monkeypatch.setattr(storage.db, "_save_profile_if_unchanged", observed)
    mode = storage.db._connection.execute("PRAGMA synchronous").fetchone()[0]
    before.storage_path = "/local/verified-copy"
    await storage.durable_update_profile(before, before.row_version)
    assert modes == [2]
    assert storage.db._connection.execute("PRAGMA synchronous").fetchone()[0] == mode
    assert (await storage.get_profile(profile.id)).storage_path == "/local/verified-copy"
    assert (await storage.get_lease(profile.id))[0] == "test-holder"


@pytest.mark.asyncio
async def test_stale_durable_write_rolls_back_and_restores_mode(storage):
    profile = Profile(name="original")
    await storage.save_profile(profile)
    stale = await storage.get_profile(profile.id)
    current = await storage.get_profile(profile.id)
    current.name = "newer"
    await storage.update_profile(current, current.row_version)
    mode = storage.db._connection.execute("PRAGMA synchronous").fetchone()[0]
    with pytest.raises(StaleWriteError):
        await storage.durable_update_profile(stale, stale.row_version)
    assert not storage.db._connection.in_transaction
    assert storage.db._connection.execute("PRAGMA synchronous").fetchone()[0] == mode
    assert (await storage.get_profile(profile.id)).name == "newer"


async def test_insert_only_cannot_overwrite_activated_or_trashed_profile(tmp_path):
    from camoufox_pm.core.database import StorageManager
    from camoufox_pm.core.models import Profile

    storage = StorageManager(str(tmp_path / "profiles.db"))
    await storage.initialize()
    try:
        profile = Profile(id="insert01", name="First", storage_path="retained")
        assert await storage.insert_profile_if_absent(profile)
        changed = profile.model_copy(update={"name": "Overwrite", "storage_path": "stale"})
        assert not await storage.insert_profile_if_absent(changed)
        assert (await storage.get_profile(profile.id)).name == "First"
        await storage.set_profile_trashed(profile.id, True)
        assert not await storage.insert_profile_if_absent(changed)
        assert await storage.get_profile(profile.id) is None
        assert (
            await storage.get_profile(profile.id, include_deleted=True)
        ).storage_path == "retained"
    finally:
        await storage.close()
