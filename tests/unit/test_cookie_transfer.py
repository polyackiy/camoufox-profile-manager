"""Validation and closed-profile atomic transfer safety without browser I/O."""

import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from camoufox_pm.core import cookie_transfer as ct
from camoufox_pm.core.backups import ProfileBackupManager
from camoufox_pm.core.database import StaleWriteError
from camoufox_pm.core.leases import ProfileLocked

SECRET = "synthetic-cookie-secret"
COOKIE = {
    "name": "token",
    "value": SECRET,
    "domain": "localhost",
    "path": "/",
    "expires": 2_000_000_000,
    "httpOnly": True,
    "secure": False,
    "sameSite": "Strict",
}


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "invalid name"},
        {"value": "bad\nsecret"},
        {"domain": "secret@localhost"},
        {"path": "secret"},
        {"secure": "true"},
        {"httpOnly": 1},
        {"sameSite": "unknown"},
        {"sameSite": "None", "secure": False},
        {"expires": float("nan")},
        {"expires": True},
        {"expires": -2},
        {"value": "x" * 4097},
        {"partitionKey": "secret"},
        {"originAttributes": "secret"},
        {"storeId": "firefox-container-1"},
        {"domain": "127.0.0.1", "hostOnly": False},
        {"domain": ".localhost"},
        {"domain": "localhost", "hostOnly": False},
        {"value": "\ud800"},
        {"path": "/\ud800"},
    ],
)
def test_invalid_json_has_safe_row_errors_and_never_echoes_values(changes):
    with pytest.raises(ct.CookieTransferError) as failure:
        ct.parse_cookies(json.dumps([{**COOKIE, **changes}]), "json", now=0)
    assert "Row 1" in str(failure.value)
    assert SECRET not in str(failure.value) and "secret@localhost" not in str(failure.value)


def test_complete_validation_precedes_expiry_and_duplicate_skips():
    items = [{**COOKIE, "expires": 1}, COOKIE, {**COOKIE, "value": "updated"}]
    parsed = ct.parse_cookies(json.dumps(items), "json", now=2)
    assert parsed.total == 3 and len(parsed.cookies) == 1
    assert parsed.cookies[0]["value"] == "updated"
    assert parsed.skipped == [
        {"row": 1, "reason": "Expired cookie"},
        {"row": 2, "reason": "Duplicate cookie; later entry wins"},
    ]
    with pytest.raises(ct.CookieTransferError):
        ct.parse_cookies(json.dumps(items + [{**COOKIE, "path": "invalid"}]), "json")


def test_browser_json_and_editthiscookie_fields_are_normalized():
    parsed = ct.parse_cookies(
        json.dumps(
            [
                {
                    "name": "a",
                    "value": "b",
                    "domain": ".LOCALHOST",
                    "hostOnly": True,
                    "session": True,
                    "expirationDate": 123,
                    "sameSite": "unspecified",
                    "storeId": "0",
                }
            ]
        ),
        "json",
    )
    assert parsed.cookies[0]["domain"] == "localhost"
    assert parsed.cookies[0]["expires"] == -1 and parsed.cookies[0]["sameSite"] == "Lax"
    assert parsed.preview()["session_count"] == 1


def test_playwright_url_infers_directory_scope_and_secure_scheme():
    cookie = ct.parse_cookies(
        json.dumps(
            [
                {
                    "name": "url-proof",
                    "value": "synthetic",
                    "url": "https://localhost/account/login",
                    "sameSite": "None",
                }
            ]
        ),
        "json",
    ).cookies[0]
    assert cookie["domain"] == "localhost" and cookie["path"] == "/account/"
    assert cookie["secure"] is True and cookie["expires"] == -1
    for fields in (
        {"domain": "localhost"},
        {"path": "/"},
        {"url": "https://secret@localhost/"},
        {"url": "https://localhost:invalid/"},
    ):
        with pytest.raises(ct.CookieTransferError):
            ct.parse_cookies(
                json.dumps(
                    [{"name": "proof", "value": SECRET, "url": "https://localhost/", **fields}]
                ),
                "json",
            )


def test_json_and_netscape_roundtrip_and_httponly_session_flags():
    cookies = [
        COOKIE,
        {
            **COOKIE,
            "name": "session",
            "domain": ".example.test",
            "expires": -1,
            "sameSite": "Lax",
            "httpOnly": True,
        },
    ]
    for format in ("json", "netscape"):
        text = ct.serialize_cookies(cookies, format)
        normalized = ct.parse_cookies(text, format, now=0).cookies
        assert normalized[0]["httpOnly"]
        assert normalized[1]["domain"] == ".example.test" and normalized[1]["expires"] == -1
        assert normalized[0]["value"] == SECRET
    assert "#HttpOnly_.example.test\tTRUE" in ct.serialize_cookies(cookies, "netscape")


@pytest.mark.parametrize(
    "text",
    [
        "not-json-cookie-secret",
        "{}",
        "[123]",
        "[",
        "\ud800",
    ],
)
def test_invalid_or_nonarray_json_does_not_echo_input(text):
    with pytest.raises(ct.CookieTransferError) as failure:
        ct.parse_cookies(text, "json")
    assert "cookie-secret" not in str(failure.value)


def test_bounds_and_netscape_overflow_are_safe():
    with pytest.raises(ct.CookieTransferError):
        ct.parse_cookies(" " * (ct.MAX_BYTES + 1), "json")
    with pytest.raises(ct.CookieTransferError):
        ct.parse_cookies(json.dumps([COOKIE] * (ct.MAX_COOKIES + 1)), "json")
    text = "localhost\tFALSE\t/\tFALSE\t" + "9" * 5000 + "\ttoken\tsecret"
    with pytest.raises(ct.CookieTransferError) as failure:
        ct.parse_cookies(text, "netscape")
    assert "secret" not in str(failure.value)


@pytest.fixture
async def transfer(profile_manager, monkeypatch):
    monkeypatch.setattr(ct, "ensure_browser_ready", lambda: None)

    async def stage(profile, directory, source, batch, mode):
        existing = []
        if source.exists():
            with sqlite3.connect(source) as connection:
                existing = json.loads(connection.execute("SELECT value FROM proof").fetchone()[0])
        rows = {} if mode == "replace" else {ct._key(cookie): cookie for cookie in existing}
        if batch is not None:
            rows.update({ct._key(cookie): cookie for cookie in batch.cookies})
        cookies = list(rows.values())
        candidate = directory / "cookies.sqlite"
        with sqlite3.connect(candidate) as connection:
            connection.execute("CREATE TABLE proof (value TEXT)")
            connection.execute("INSERT INTO proof VALUES (?)", (json.dumps(cookies),))
        return cookies, candidate

    monkeypatch.setattr(ct, "_stage_store", stage)
    return ct.CookieTransfer(profile_manager, ProfileBackupManager(profile_manager))


@pytest.mark.asyncio
async def test_merge_replace_snapshot_backup_and_portable_export(transfer):
    profile = await transfer.manager.create_profile("cookies", generate_fingerprint=False)
    first = await transfer.import_cookies(profile.id, json.dumps([COOKIE]), "json", "merge", 0)
    assert first["imported"] == 1 and first["row_version"] == 1 and first["backup_id"] is None
    updated = {**COOKIE, "name": "new", "value": "new-value"}
    merged = await transfer.import_cookies(profile.id, json.dumps([updated]), "json", "merge", 1)
    assert merged["row_version"] == 2
    text = await transfer.export_cookies(profile.id, "json", 2)
    assert {cookie["name"] for cookie in json.loads(text)} == {"token", "new"}
    replaced = await transfer.import_cookies(profile.id, "[]", "json", "replace", 2, True)
    assert replaced["backup_id"] and replaced["row_version"] == 3
    assert await transfer.export_cookies(profile.id, "json", 3) == "[]"
    assert transfer.backups.list_backups()[0]["reason"] == "before_cookie_replace"
    restored = await transfer.backups.restore_backup(replaced["backup_id"])
    with sqlite3.connect(Path(restored.storage_path) / "cookies.sqlite") as connection:
        assert {
            cookie["name"]
            for cookie in json.loads(connection.execute("SELECT value FROM proof").fetchone()[0])
        } == {"token", "new"}


@pytest.mark.asyncio
async def test_stale_version_live_lease_and_confirmation_leave_cookie_store_unchanged(transfer):
    profile = await transfer.manager.create_profile("closed", generate_fingerprint=False)
    with pytest.raises(ct.CookieTransferError):
        await transfer.import_cookies(profile.id, "[]", "json", "replace", 0)
    with pytest.raises(StaleWriteError):
        await transfer.import_cookies(profile.id, "[]", "json", "merge", 99)
    transfer.manager.browser_sessions._starting[profile.id] = 1
    try:
        with pytest.raises(ProfileLocked):
            await transfer.import_cookies(profile.id, "[]", "json", "merge", 0)
    finally:
        transfer.manager.browser_sessions._starting.clear()
    await transfer.manager.storage.acquire_lease(profile.id, "remote", 300)
    with pytest.raises(ProfileLocked):
        await transfer.export_cookies(profile.id, "json", 0)
    assert not Path(profile.storage_path, "cookies.sqlite").exists()


@pytest.mark.asyncio
async def test_failed_backup_prevents_replace_before_staging(transfer, monkeypatch):
    profile = await transfer.manager.create_profile("safe", generate_fingerprint=False)
    stage = AsyncMock()
    monkeypatch.setattr(ct, "_stage_store", stage)
    transfer.backups.create_locked_backup = AsyncMock(side_effect=OSError("backup failed"))
    with pytest.raises(ct.CookieTransferError):
        await transfer.import_cookies(profile.id, "[]", "json", "replace", 0, True)
    stage.assert_not_called()
    assert (await transfer.manager.get_profile(profile.id)).row_version == 0


@pytest.mark.asyncio
async def test_parser_failure_has_no_browser_or_backup_side_effect(transfer, monkeypatch):
    profile = await transfer.manager.create_profile("safe", generate_fingerprint=False)
    stage = AsyncMock()
    monkeypatch.setattr(ct, "_stage_store", stage)
    with pytest.raises(ct.CookieTransferError):
        await transfer.import_cookies(
            profile.id, json.dumps([COOKIE, {"value": SECRET}]), "json", "replace", 0, True
        )
    stage.assert_not_called()
    assert transfer.backups.list_backups() == []


def test_pending_handoff_survives_interruption_until_durable_ack(tmp_path):
    database = tmp_path / "cookies.sqlite"
    payload = {"replace": False, "cookies": [{**COOKIE, "expires": -1}]}
    ct._set_pending(database, payload)
    persisted = ct._read_pending(database)
    assert persisted["nonce"]
    assert ct.take_pending_cookies(tmp_path) == persisted
    # An interrupted launch never consumed the durable handoff.
    assert ct.take_pending_cookies(tmp_path) == persisted
    ct._write_ack(tmp_path, persisted["nonce"])
    assert ct._read_pending(database) is None
    assert ct.take_pending_cookies(tmp_path) is None
    assert ct._read_pending(database) is None
    # Importing the same values receives a new identity, never a stale ack.
    ct._set_pending(database, payload)
    assert ct._read_pending(database)["nonce"] != persisted["nonce"]
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE cpm_cookie_transfer SET payload = ?", ('{"cookies":"secret"}',))
    connection.close()
    with pytest.raises(ct.CookieTransferError) as failure:
        ct.take_pending_cookies(tmp_path)
    assert "secret" not in str(failure.value)


def test_offline_sqlite_handles_close_before_publication_and_after_validation_error(
    tmp_path, monkeypatch
):
    # SQLite's transaction context manager does not close the handle. Retain
    # strong references so this test cannot pass through garbage collection;
    # leaked handles prevent replacement and temporary cleanup on Windows.
    connect = sqlite3.connect
    opened = []

    def tracked_connect(*args, **kwargs):
        connection = connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(ct.sqlite3, "connect", tracked_connect)
    source, candidate, target = (tmp_path / name for name in ("source", "candidate", "target"))
    ct._set_pending(source, {"replace": False, "cookies": [{**COOKIE, "expires": -1}]})
    pending = ct._read_pending(source)
    ct._copy_store(source, candidate)
    ct._set_pending(target, None)
    ct._publish_store(candidate, target)
    assert ct._read_pending(target) == pending
    with connect(target) as invalid:
        invalid.execute("UPDATE cpm_cookie_transfer SET payload='invalid'")
    invalid.close()
    with pytest.raises(ct.CookieTransferError):
        ct._read_pending(target)
    for connection in opened:
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            connection.execute("SELECT 1")


@pytest.mark.asyncio
async def test_pending_application_uses_browser_api_only_and_preserves_session_flags(tmp_path):
    browser = type("Browser", (), {"clear_cookies": AsyncMock(), "add_cookies": AsyncMock()})()
    session = {**COOKIE, "expires": -1}
    await ct.apply_pending_cookies(
        browser, {"replace": True, "cookies": [session], "nonce": "a" * 32}, tmp_path
    )
    browser.clear_cookies.assert_awaited_once()
    assert browser.add_cookies.call_args.args[0][0]["expires"] == -1


@pytest.mark.asyncio
async def test_cancel_during_ack_restores_retryability_before_untracking(
    profile_manager, monkeypatch
):
    import asyncio
    import threading
    from unittest.mock import Mock

    from camoufox_pm.core import browser_session as bs

    profile = await profile_manager.create_profile("ack cancellation", generate_fingerprint=False)
    directory = Path(profile.storage_path)
    database = directory / "cookies.sqlite"
    ct._set_pending(database, {"replace": False, "cookies": [{**COOKIE, "expires": -1}]})
    persisted = ct._read_pending(database)
    started, finish = threading.Event(), threading.Event()
    write_ack = ct._write_ack

    def blocked_ack(*args):
        started.set()
        assert finish.wait(3)
        write_ack(*args)

    monkeypatch.setattr(ct, "_write_ack", blocked_ack)
    browser = type(
        "Browser", (), {"clear_cookies": AsyncMock(), "add_cookies": AsyncMock(), "on": Mock()}
    )()
    camoufox = type(
        "Camoufox", (), {"start": AsyncMock(return_value=browser), "__aexit__": AsyncMock()}
    )()
    monkeypatch.setattr(bs, "CAMOUFOX_AVAILABLE", True)
    monkeypatch.setattr(bs, "AsyncCamoufox", lambda **options: camoufox)
    manager = bs.BrowserSessionManager()
    options = {"persistent_context": True, "user_data_dir": str(directory)}
    launch = asyncio.create_task(manager.launch(profile.id, options))
    while not started.is_set():
        await asyncio.sleep(0.005)
    launch.cancel()
    await asyncio.sleep(0)
    assert manager.is_live(profile.id)
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await launch
    assert not manager.is_live(profile.id)
    camoufox.__aexit__.assert_awaited_once()
    assert ct.take_pending_cookies(directory) == persisted
    assert not (directory / ct._ACK_NAME).exists()
    # A later successful launch acknowledges it exactly once.
    await manager.launch(profile.id, options)
    assert ct._read_pending(database) is None
    await manager.close(profile.id)
    assert ct.take_pending_cookies(directory) is None
