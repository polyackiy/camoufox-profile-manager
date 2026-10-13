from contextlib import closing
from pathlib import Path

import pytest

from camoufox_pm.core.maintenance import MaintenanceBlocked, MaintenanceGate


def test_gate_drains_and_blocks_other_instances(tmp_path: Path):
    gate = MaintenanceGate(tmp_path / "gate.db")
    other = MaintenanceGate(tmp_path / "gate.db")
    with gate.operation():
        with pytest.raises(MaintenanceBlocked):
            other.acquire("update")
        with pytest.raises(MaintenanceBlocked):
            # Use a distinct context: the current admitted operation is allowed
            # to finish, so exercise cross-process admission with a new context.
            from contextvars import Context

            Context().run(lambda: other.operation().__enter__())
    other.acquire("update")
    assert gate.blocked
    with pytest.raises(MaintenanceBlocked):
        gate.release("wrong")
    gate.release("update")
    with other.operation():
        pass


def test_interrupted_gate_survives_reopening(tmp_path: Path):
    path = tmp_path / "gate.db"
    MaintenanceGate(path).acquire("interrupted")
    reopened = MaintenanceGate(path)
    assert reopened.blocked
    with pytest.raises(MaintenanceBlocked), reopened.operation():
        pass
    reopened.release("interrupted")
    assert not reopened.blocked


def test_preparation_failure_persists_gate_for_explicit_cancel(tmp_path: Path):
    from camoufox_pm.core.update_service import UpdateService

    gate = MaintenanceGate(tmp_path / "gate.db")
    service = UpdateService(tmp_path / "updates", gate)
    with pytest.raises(MaintenanceBlocked):
        service.prepare(
            db_path=tmp_path / "app.db",
            paths={},
            verified_package=tmp_path / "package",
            verify_package=lambda path: None,
            offline_check=lambda: False,
            browser_version="135.0",
            target_version="1.0.0",
        )
    assert service.status()["state"] == "preparing"
    assert gate.blocked
    service.cancel_preparation()
    assert service.status()["state"] == "cancelled"
    assert not gate.blocked


def test_unverified_package_never_enters_maintenance(tmp_path: Path):
    from camoufox_pm.core.update_service import UpdateService

    gate = MaintenanceGate(tmp_path / "gate.db")
    service = UpdateService(tmp_path / "updates", gate)

    def refuse(path):
        raise ValueError("Untrusted publisher")

    with pytest.raises(ValueError, match="Untrusted publisher"):
        service.prepare(
            db_path=tmp_path / "app.db",
            paths={},
            verified_package=tmp_path / "package",
            verify_package=refuse,
            offline_check=lambda: True,
            browser_version="135.0",
            target_version="1.0.0",
        )
    assert service.status()["state"] == "idle"
    assert not gate.blocked


@pytest.mark.asyncio
async def test_child_task_cannot_inherit_admission(tmp_path: Path):
    import asyncio

    gate = MaintenanceGate(tmp_path / "gate.db")
    with gate.operation():
        with pytest.raises(MaintenanceBlocked):
            gate.acquire("update")

        async def child():
            with gate.operation():
                pass

        with pytest.raises(MaintenanceBlocked):
            await asyncio.create_task(child())
    gate.release("update")


@pytest.mark.asyncio
async def test_api_request_holds_admission_until_complete(tmp_path: Path):
    import asyncio

    from camoufox_pm.api.middleware.maintenance import MaintenanceMiddleware

    gate = MaintenanceGate(tmp_path / "gate.db")
    started, finish = asyncio.Event(), asyncio.Event()
    messages = []

    async def application(scope, receive, send):
        started.set()
        await finish.wait()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def send(message):
        messages.append(message)

    middleware = MaintenanceMiddleware(application, gate)
    scope = {"type": "http", "path": "/api/v1/profiles", "method": "POST"}
    request = asyncio.create_task(middleware(scope, None, send))
    await started.wait()
    with pytest.raises(MaintenanceBlocked):
        gate.acquire("update")
    await middleware(scope, None, send)
    assert messages[0]["status"] == 503
    finish.set()
    await request
    gate.acquire("update")
    gate.release("update")


def test_cancel_refuses_running_preparation(tmp_path: Path):
    from camoufox_pm.core.update_service import UpdateService

    gate = MaintenanceGate(tmp_path / "gate.db")
    first = UpdateService(tmp_path / "updates", gate)
    second = UpdateService(tmp_path / "updates", gate)
    with first._control():
        with pytest.raises(MaintenanceBlocked, match="already running"):
            second.cancel_preparation()


@pytest.mark.asyncio
async def test_recovery_routes_hide_paths_and_cancel_only_preparation(tmp_path: Path, monkeypatch):
    from fastapi import HTTPException

    from camoufox_pm.api.routes.system import cancel_update_preparation, update_recovery_status
    from camoufox_pm.core import maintenance
    from camoufox_pm.core.update_service import UpdateService

    gate = MaintenanceGate(tmp_path / "control" / "maintenance.sqlite3")
    monkeypatch.setattr(maintenance, "configured_gate", lambda: gate)
    service = UpdateService(gate.path.parent, gate)
    service._save(
        {
            "transaction_id": "test",
            "state": "prepared",
            "package": "/secret/package",
            "checkpoint": "/secret/checkpoint",
            "paths": {"key": "/secret/key"},
        }
    )
    gate.acquire("test")
    status = await update_recovery_status()
    assert "package" not in status and "paths" not in status and "checkpoint" not in status
    assert status["can_cancel"] and status["maintenance_active"]
    assert (await cancel_update_preparation())["state"] == "cancelled"
    service._save({"transaction_id": "test", "state": "replaced"})
    with pytest.raises(HTTPException) as error:
        await cancel_update_preparation()
    assert error.value.status_code == 409


def test_manager_registry_requires_process_exit(tmp_path: Path):
    gate = MaintenanceGate(tmp_path / "gate.db")
    gate.register_manager()
    try:
        assert not gate.managers_stopped()
    finally:
        gate.unregister_manager()
    assert gate.managers_stopped()


def test_operation_cleanup_proves_dead_local_identity_only(tmp_path: Path):
    import os
    import socket
    import sqlite3

    import psutil

    gate = MaintenanceGate(tmp_path / "gate.db")
    # The same PID with a different birth time is an interrupted prior process.
    with closing(sqlite3.connect(gate.path)) as db, db:
        db.execute(
            "INSERT INTO operations VALUES (?,?,?,?)",
            ("dead", os.getpid(), psutil.Process().create_time() - 100, socket.gethostname()),
        )
    gate.acquire("update")
    gate.release("update")
    with closing(sqlite3.connect(gate.path)) as db, db:
        db.execute(
            "INSERT INTO operations VALUES (?,?,?,?)", ("remote", os.getpid(), 0, "other-machine")
        )
    with pytest.raises(MaintenanceBlocked):
        gate.acquire("update")
    gate.release("update")


@pytest.mark.asyncio
async def test_recovery_auth_does_not_delete_expired_session():
    from datetime import datetime, timedelta
    from unittest.mock import AsyncMock

    from camoufox_pm.core.auth import validate_session

    storage = AsyncMock()
    storage.get_session.return_value = {
        "expires_at": (datetime.now() - timedelta(seconds=1)).isoformat(),
        "token_hash": "expired",
        "username": "user",
    }
    assert await validate_session(storage, "token", read_only=True) is None
    storage.delete_session.assert_not_awaited()
    assert await validate_session(storage, "token") is None
    storage.delete_session.assert_awaited_once_with("expired")


def test_access_denied_process_is_not_pruned(tmp_path: Path, monkeypatch):
    import os
    import socket
    import sqlite3

    import psutil

    gate = MaintenanceGate(tmp_path / "gate.db")
    with closing(sqlite3.connect(gate.path)) as db, db:
        db.execute(
            "INSERT INTO operations VALUES (?,?,?,?)",
            ("uncertain", os.getpid(), 0, socket.gethostname()),
        )

    def denied(pid):
        raise psutil.AccessDenied(pid)

    monkeypatch.setattr(psutil, "Process", denied)
    with pytest.raises(MaintenanceBlocked):
        gate.acquire("update")
    gate.release("update")


def test_cancel_cannot_bypass_helper_replacement_journal(tmp_path: Path):
    from camoufox_pm.core.update_service import UpdateService

    gate = MaintenanceGate(tmp_path / "control" / "maintenance.sqlite3")
    service = UpdateService(gate.path.parent, gate)
    service._save({"transaction_id": "update", "state": "prepared"})
    (service.root / "replacement.json").write_text('{"state":"moving_old"}')
    gate.acquire("update")
    with pytest.raises(MaintenanceBlocked, match="helper journal"):
        service.cancel_preparation()
    assert gate.blocked


def test_interrupted_offline_checkpoint_can_be_cancelled_without_deleting_copy(tmp_path: Path):
    from camoufox_pm.core.update_service import UpdateService

    gate = MaintenanceGate(tmp_path / "control" / "maintenance.sqlite3")
    service = UpdateService(gate.path.parent, gate)
    owner = "checkpoint-" + "a" * 32
    copy = service.root / "checkpoints" / ("a" * 32)
    copy.mkdir(parents=True)
    (copy / "partial").write_text("retained")
    gate.acquire(owner)
    reopened = UpdateService(gate.path.parent, MaintenanceGate(gate.path))
    assert reopened.status()["state"] == "checkpoint_interrupted"
    reopened.cancel_abandoned_checkpoint()
    assert not gate.blocked
    assert reopened.status()["state"] == "idle"
    assert (copy / "partial").read_text() == "retained"
    with gate.operation():
        pass


@pytest.mark.parametrize("owner", ["restore-" + "a" * 32, "checkpoint-invalid", "replacement"])
def test_checkpoint_cancel_refuses_other_owner(tmp_path: Path, owner):
    from camoufox_pm.core.update_service import UpdateService

    gate = MaintenanceGate(tmp_path / "control" / "maintenance.sqlite3")
    service = UpdateService(gate.path.parent, gate)
    gate.acquire(owner)
    with pytest.raises(MaintenanceBlocked):
        service.cancel_abandoned_checkpoint()
    assert gate.blocked


def test_checkpoint_cancel_refuses_active_control_and_helper_journal(tmp_path: Path):
    from camoufox_pm.core.update_service import UpdateService

    gate = MaintenanceGate(tmp_path / "control" / "maintenance.sqlite3")
    service = UpdateService(gate.path.parent, gate)
    gate.acquire("checkpoint-" + "a" * 32)
    with service._control():
        with pytest.raises(MaintenanceBlocked, match="already running"):
            service.cancel_abandoned_checkpoint()
    (service.root / "replacement.json").write_text("{}")
    with pytest.raises(MaintenanceBlocked, match="helper journal"):
        service.cancel_abandoned_checkpoint()
    assert gate.blocked


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["/apply", "/resume", "/rollback", "/download", "/other"])
async def test_exact_update_controls_pass_maintenance_but_download_does_not(tmp_path, suffix):
    from camoufox_pm.api.middleware.maintenance import MaintenanceMiddleware

    gate = MaintenanceGate(tmp_path / "gate.db")
    gate.acquire("update")
    messages = []

    async def application(scope, receive, send):
        await send({"type": "http.response.start", "status": 401, "headers": []})
        await send({"type": "http.response.body", "body": b"auth remains required"})

    async def send(message):
        messages.append(message)

    await MaintenanceMiddleware(application, gate)(
        {"type": "http", "path": "/api/v1/system/update-recovery" + suffix, "method": "POST"},
        None,
        send,
    )
    assert messages[0]["status"] == (401 if suffix in ("/apply", "/resume", "/rollback") else 503)
