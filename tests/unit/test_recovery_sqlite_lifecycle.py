"""Recovery key probes release SQLite before publication and on rejected input."""

import sqlite3
from contextlib import closing

import pytest

from camoufox_pm.config import get_settings
from camoufox_pm.sync import recovery_workflow as recovery


def assert_closed(connection):
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute("SELECT 1")


@pytest.mark.parametrize(
    ("contents", "create", "error"),
    [
        ("empty", False, None),
        ("plain", True, None),
        ("encrypted", True, recovery.RecoveryWorkflowError),
        ("wrong_schema", True, sqlite3.DatabaseError),
        ("corrupt", True, sqlite3.DatabaseError),
    ],
)
def test_recovery_key_probe_closes_owned_database(tmp_path, monkeypatch, contents, create, error):
    database = tmp_path / "profiles.db"
    connect = sqlite3.connect
    if contents == "corrupt":
        database.write_bytes(b"not a sqlite database")
    else:
        with closing(connect(database)) as connection, connection:
            if contents == "wrong_schema":
                connection.execute("CREATE TABLE profiles (unrelated TEXT)")
            elif contents != "empty":
                connection.execute("CREATE TABLE profiles (proxy_config TEXT)")
                value = '{"password":"enc:existing"}' if contents == "encrypted" else "{}"
                connection.execute("INSERT INTO profiles VALUES (?)", (value,))
    before = database.read_bytes()
    # Retain references so collection cannot make an unclosed handle pass.
    opened = []

    def tracked_connect(*args, **kwargs):
        connection = connect(*args, **kwargs)
        opened.append(connection)
        return connection

    publish = recovery._write

    def publish_after_close(*args, **kwargs):
        assert len(opened) == 1
        assert_closed(opened[0])
        return publish(*args, **kwargs)

    monkeypatch.setenv("CPM_SECRET_KEY", "")
    monkeypatch.setattr(sqlite3, "connect", tracked_connect)
    monkeypatch.setattr(recovery, "_write", publish_after_close)
    try:
        if error:
            with pytest.raises(error):
                recovery._target_key(database, create=create)
        else:
            recovery._target_key(database, create=create)
        assert len(opened) == 1
        assert_closed(opened[0])
        assert database.read_bytes() == before
        assert (tmp_path / "secret.key").exists() is (create and error is None)
    finally:
        for connection in opened:
            connection.close()
        get_settings.cache_clear()
