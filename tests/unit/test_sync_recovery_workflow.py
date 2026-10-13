"""Sealed offline recovery creates only a fresh local profile."""
# ruff: noqa: F811

from pathlib import Path
from types import SimpleNamespace

import pytest

from camoufox_pm.core.database import StorageManager
from camoufox_pm.core.maintenance import MaintenanceGate
from camoufox_pm.core.models import Profile, ProxyConfig
from camoufox_pm.core.update_permissions import create_private_directory
from camoufox_pm.sync.recovery import create_recovery
from camoufox_pm.sync.recovery_workflow import RecoveryWorkflowError, export_bundle, restore_bundle
from tests.unit.test_sync_apply import area  # noqa: F401
from tests.unit.test_sync_runtime import runtime  # noqa: F401


@pytest.fixture(autouse=True)
def real_relay(runtime, tmp_path):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr

    from camoufox_pm.sync.client import RelayClient
    from camoufox_pm.sync.relay import create_relay_app

    active, _ = runtime
    _, owner = active.service._read("owner-user")
    token = "synthetic-workflow-registration-token"
    app = create_relay_app(
        tmp_path.resolve() / "real-relay",
        relay_url=owner.root.relay_url,
        registration_token=SecretStr(token),
    )
    with TestClient(app, base_url=owner.root.relay_url) as http:

        def factory(root, device_id, keys):
            return RelayClient(root, device_id, keys, transport=http._transport)

        active.client_factory = factory
        authority, keys = active.service._keys(owner, None)
        client = factory(owner.root, owner.device_id, keys)
        client.register(authority, owner.membership, token)
        try:
            yield
        finally:
            client.close()


@pytest.fixture
async def destination(tmp_path):
    root = tmp_path.resolve() / "clean-installation"
    create_private_directory(root)
    create_private_directory(root / "profiles")
    storage = StorageManager(str(root / "profiles.db"))
    await storage.initialize()
    manager = SimpleNamespace(storage=storage, profiles_dir=root / "profiles", data_dir=root)
    yield manager, MaintenanceGate(root / "maintenance.sqlite3")
    await storage.close()


async def sealed(runtime, area, tmp_path):
    runtime, _ = runtime
    profile = await area.manager.storage.get_profile(area.profile.id)
    profile.fingerprint = {"navigator.hardwareConcurrency": 8}
    profile.proxy = ProxyConfig(
        type="http",
        server="proxy.example:8080",
        username="synthetic",
        password="synthetic-proxy-secret",
    )
    await area.manager.storage.save_profile(profile)
    await runtime.enable("owner-user", area.profile.id)
    _, owner = runtime.service._read("owner-user")
    authority, _ = runtime.service._keys(owner, None)
    package, secret = create_recovery(owner.root, authority)
    output = tmp_path.resolve() / "sealed-export"
    await export_bundle(runtime, area.profile.id, output)
    return package, secret, output


async def restore(destination, area, package, secret, output, **kwargs):
    manager, gate = destination
    return await restore_bundle(
        manager,
        gate,
        package,
        secret,
        (output / "evidence.json").read_bytes(),
        output / "snapshot.ciphertext",
        acknowledge_historical_fork=True,
        compatibility=lambda: area.compatibility,
        **kwargs,
    )


async def test_export_and_offline_restore_fresh_identity_without_namespace(
    runtime, area, destination, tmp_path
):
    package, secret, output = await sealed(runtime, area, tmp_path)
    assert {p.name for p in output.iterdir()} == {"evidence.json", "snapshot.ciphertext"}
    assert b"synthetic-proxy-secret" not in (output / "evidence.json").read_bytes()
    assert b"old local cookies" not in (output / "snapshot.ciphertext").read_bytes()
    control_before = runtime[0].service.control.get("profile/" + area.profile.id)
    recovered = await restore(destination, area, package, secret, output)
    manager, _ = destination
    assert recovered.id != area.profile.id
    assert (Path(recovered.storage_path) / "cookies.sqlite").read_bytes() == b"old local cookies"
    assert recovered.fingerprint == {"navigator.hardwareConcurrency": 8}
    assert recovered.proxy.password == "synthetic-proxy-secret"
    persisted = await manager.storage.get_profile(recovered.id)
    assert persisted.proxy.password == "synthetic-proxy-secret"
    assert not (manager.data_dir / "sync-control").exists()
    assert not (Path(recovered.storage_path) / ".camoufox-pm-sync.json").exists()
    assert await manager.storage.list_schedules(recovered.id) == []
    assert await manager.storage.list_users() == []
    assert runtime[0].service.control.get("profile/" + area.profile.id) == control_before
    assert not list(manager.profiles_dir.glob("recovery-*"))
    second = await restore(destination, area, package, secret, output)
    assert second.id != recovered.id


async def test_wrong_secret_preserves_existing_profiles(runtime, area, destination, tmp_path):
    package, secret, output = await sealed(runtime, area, tmp_path)
    manager, _ = destination
    existing = Profile(name="Existing local data")
    await manager.storage.insert_profile_if_absent(existing)
    with pytest.raises(RecoveryWorkflowError):
        await restore(destination, area, package, "X" * 43, output)
    assert (await manager.storage.get_profile(existing.id)).name == "Existing local data"
    assert not list(manager.profiles_dir.iterdir())


async def test_acknowledgement_required_before_output(runtime, area, destination, tmp_path):
    package, secret, output = await sealed(runtime, area, tmp_path)
    manager, gate = destination
    with pytest.raises(RecoveryWorkflowError):
        await restore_bundle(
            manager,
            gate,
            package,
            secret,
            (output / "evidence.json").read_bytes(),
            output / "snapshot.ciphertext",
            acknowledge_historical_fork=False,
            compatibility=lambda: area.compatibility,
        )
    assert not list(manager.profiles_dir.iterdir())


async def test_maintenance_blocks_restore_and_existing_export_target_preserved(
    runtime, area, destination, tmp_path
):
    package, secret, output = await sealed(runtime, area, tmp_path)
    _, gate = destination
    gate.acquire("update-in-progress")
    try:
        with pytest.raises(RecoveryWorkflowError):
            await restore(destination, area, package, secret, output)
    finally:
        gate.release("update-in-progress")
    before = (output / "evidence.json").read_bytes()
    with pytest.raises(RecoveryWorkflowError):
        await export_bundle(runtime[0], area.profile.id, output)
    assert (output / "evidence.json").read_bytes() == before


async def test_collision_does_not_overwrite_database_profile(
    runtime, area, destination, tmp_path, monkeypatch
):
    package, secret, output = await sealed(runtime, area, tmp_path)
    manager, _ = destination
    existing = Profile(name="Never overwrite this")
    await manager.storage.insert_profile_if_absent(existing)
    monkeypatch.setattr(
        "camoufox_pm.sync.recovery_workflow.Profile",
        lambda **fields: Profile(id=existing.id, **fields),
    )
    with pytest.raises(RecoveryWorkflowError):
        await restore(destination, area, package, secret, output)
    assert (await manager.storage.get_profile(existing.id)).name == "Never overwrite this"
    assert not list(manager.profiles_dir.iterdir())


async def test_demoted_local_owner_cannot_export(runtime, area, tmp_path):
    await runtime[0].enable("owner-user", area.profile.id)
    area.manager.storage.db._connection.execute(
        "UPDATE users SET role='viewer' WHERE id='owner-user'"
    )
    area.manager.storage.db._connection.commit()
    output = tmp_path / "forbidden"
    with pytest.raises(RecoveryWorkflowError):
        await export_bundle(runtime[0], area.profile.id, output)
    assert not output.exists()


async def test_existing_directory_is_never_removed_on_id_collision(
    runtime, area, destination, tmp_path, monkeypatch
):
    package, secret, output = await sealed(runtime, area, tmp_path)
    manager, _ = destination
    existing = Profile(name="Directory owner")
    directory = manager.profiles_dir / ("profile_" + existing.id)
    create_private_directory(directory)
    (directory / "keep").write_bytes(b"untouched")
    monkeypatch.setattr(
        "camoufox_pm.sync.recovery_workflow.Profile",
        lambda **fields: Profile(id=existing.id, **fields),
    )
    with pytest.raises(RecoveryWorkflowError):
        await restore(destination, area, package, secret, output)
    assert (directory / "keep").read_bytes() == b"untouched"


async def test_incompatible_installed_browser_does_not_import(runtime, area, destination, tmp_path):
    package, secret, output = await sealed(runtime, area, tmp_path)
    manager, gate = destination
    with pytest.raises(RecoveryWorkflowError):
        await restore_bundle(
            manager,
            gate,
            package,
            secret,
            (output / "evidence.json").read_bytes(),
            output / "snapshot.ciphertext",
            acknowledge_historical_fork=True,
            compatibility=lambda: area.compatibility.model_copy(
                update={"browser_build": "different-build"}
            ),
        )
    assert not list(manager.profiles_dir.iterdir())


def test_cli_secrets_are_prompted_not_command_arguments():
    from camoufox_pm.sync.recovery_workflow import main

    with pytest.raises(SystemExit) as error:
        main(
            [
                "restore",
                "--db",
                "new.db",
                "--package",
                "package",
                "--evidence",
                "evidence",
                "--ciphertext",
                "cipher",
            ]
        )
    assert error.value.code == 2


def test_restore_key_uses_destination_and_rejects_conflicting_environment(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet

    from camoufox_pm.config import get_settings
    from camoufox_pm.sync.recovery_workflow import _target_key

    monkeypatch.delenv("CPM_SECRET_KEY", raising=False)
    try:
        _target_key(tmp_path / "profiles.db")
        key = (tmp_path / "secret.key").read_text()
        assert get_settings().secret_key == key
        monkeypatch.setenv("CPM_SECRET_KEY", Fernet.generate_key().decode())
        with pytest.raises(RecoveryWorkflowError):
            _target_key(tmp_path / "profiles.db")
        assert (tmp_path / "secret.key").read_text() == key
    finally:
        get_settings.cache_clear()


def test_existing_encrypted_database_without_key_is_not_rekeyed(tmp_path, monkeypatch):
    import sqlite3

    from camoufox_pm.sync.recovery_workflow import _target_key

    monkeypatch.delenv("CPM_SECRET_KEY", raising=False)
    path = tmp_path / "profiles.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE profiles (proxy_config TEXT)")
        db.execute("INSERT INTO profiles VALUES (?)", ('{"password":"enc:existing"}',))
    with pytest.raises(RecoveryWorkflowError):
        _target_key(path)
    assert not (tmp_path / "secret.key").exists()


@pytest.mark.parametrize("command", ["restore", "export"])
async def test_cli_uses_actual_manager_maintenance_gate(tmp_path, command):
    from argparse import Namespace

    from camoufox_pm.core.maintenance import MaintenanceBlocked
    from camoufox_pm.sync.recovery_workflow import _execute

    database = tmp_path / "profiles.db"
    database.touch()
    gate = MaintenanceGate(tmp_path / "update-control" / "maintenance.sqlite3")
    gate.acquire("real-manager-update")
    try:
        with pytest.raises(MaintenanceBlocked):
            await _execute(Namespace(db=database, command=command))
        assert not (tmp_path / "secret.key").exists()
    finally:
        gate.release("real-manager-update")


def test_export_does_not_generate_destination_key(tmp_path, monkeypatch):
    from camoufox_pm.config import get_settings
    from camoufox_pm.sync.recovery_workflow import _target_key

    monkeypatch.delenv("CPM_SECRET_KEY", raising=False)
    try:
        _target_key(tmp_path / "profiles.db", create=False)
        assert not (tmp_path / "secret.key").exists()
        assert not get_settings().secret_key
    finally:
        get_settings.cache_clear()


async def test_preexisting_recovery_staging_is_not_deleted(
    runtime, area, destination, tmp_path, monkeypatch
):
    from uuid import UUID

    package, secret, output = await sealed(runtime, area, tmp_path)
    manager, _ = destination
    identifier = UUID("11111111-1111-4111-8111-111111111111")
    staging = manager.profiles_dir / ("recovery-" + str(identifier))
    create_private_directory(staging)
    (staging / "keep").write_bytes(b"existing private recovery data")
    monkeypatch.setattr("camoufox_pm.sync.recovery_workflow.uuid4", lambda: identifier)
    with pytest.raises(RecoveryWorkflowError):
        await restore(destination, area, package, secret, output)
    assert (staging / "keep").read_bytes() == b"existing private recovery data"
