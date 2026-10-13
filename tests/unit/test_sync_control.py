"""Cross-process CAS, journal recovery and fail-closed control storage tests."""

import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError

import pytest

from camoufox_pm.sync.control import (
    MAX_PAYLOAD,
    ControlConflict,
    ControlError,
    ControlMutation,
    ControlStore,
)


@pytest.fixture
def store(tmp_path):
    return ControlStore(tmp_path.resolve() / "control")


def test_lifecycle_reopen_and_immutable_records(store):
    assert store.get("owner/root") is None
    record = store.create("owner/root", {"epoch": 1, "members": ["a"]})
    assert record.revision == 1
    with pytest.raises(FrozenInstanceError):
        record.revision = 7
    payload = record.payload
    payload["members"].append("attacker")
    assert record.payload == {"epoch": 1, "members": ["a"]}
    updated = store.update(record.key, {"epoch": 2}, record.revision)
    assert updated.revision == 2
    assert ControlStore(store.directory).get(record.key) == updated
    with pytest.raises(ControlConflict):
        store.update(record.key, {"epoch": 3}, record.revision)
    with pytest.raises(ControlConflict):
        store.create(record.key, {})


def test_literal_prefix_not_like_pattern(store):
    store.create("a_b/one", {})
    store.create("axb/two", {})
    store.create("other", {})
    assert [r.key for r in store.list("a_b")] == ["a_b/one"]
    assert len(store.list()) == 3


def test_atomic_invitation_consume_and_rollback(store):
    invite = store.create("invite/id", {"state": "pending"})
    store.create("member/existing", {})
    with pytest.raises(ControlConflict):
        store.batch(
            [
                ControlMutation(invite.key, {"state": "consumed"}, invite.revision),
                ControlMutation("member/existing", {"approved": True}),
            ]
        )
    assert store.get(invite.key).payload["state"] == "pending"
    mutations = [
        ControlMutation(invite.key, {"state": "consumed"}, invite.revision),
        ControlMutation("member/new", {"approved": True}),
    ]
    store.batch(mutations)
    with pytest.raises(ControlConflict):
        store.batch(mutations)
    assert store.get(invite.key).revision == 2
    assert store.get("member/new").revision == 1


def test_two_connections_one_winner(store):
    store.create("checkout/profile", {"holder": None})

    def claim(index):
        try:
            store.update("checkout/profile", {"holder": index}, 1)
            return True
        except ControlConflict:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(claim, [1, 2])) == [False, True]
    assert store.get("checkout/profile").revision == 2


def test_cross_process_one_winner(store):
    store.create("checkout/profile", {"holder": None})
    script = """
import sys
from pathlib import Path
from camoufox_pm.sync.control import ControlStore, ControlConflict
try:
    ControlStore(Path(sys.argv[1])).update("checkout/profile", {"holder":sys.argv[2]}, 1)
except ControlConflict:
    sys.exit(3)
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(store.directory), str(index)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for index in [1, 2]
    ]
    results = []
    for process in processes:
        _, stderr = process.communicate(timeout=30)
        assert not stderr, stderr.decode()
        results.append(process.returncode)
    assert sorted(results) == [0, 3]


def test_crash_uncommitted_transaction_recovers(store):
    store.create("stable", {"value": "original"})
    script = """
import os, sqlite3, sys
connection=sqlite3.connect(sys.argv[1])
connection.execute("PRAGMA synchronous=FULL")
connection.execute("BEGIN IMMEDIATE")
connection.execute("UPDATE records SET payload='{}',revision=2 WHERE key='stable'")
os._exit(0)
"""
    subprocess.run([sys.executable, "-c", script, str(store.path)], check=True, timeout=30)
    reopened = ControlStore(store.directory)
    assert reopened.get("stable").payload == {"value": "original"}
    assert reopened.get("stable").revision == 1


@pytest.mark.parametrize("key", ["", "../bad?", "a%", "é", "a" * 161, None])
def test_invalid_keys(store, key):
    with pytest.raises(ControlError):
        store.create(key, {})


@pytest.mark.parametrize(
    "payload",
    [[], {"x": float("nan")}, {"x": 2**54}, {1: "x"}, {"x": "a" * MAX_PAYLOAD}, {"x": object()}],
)
def test_invalid_payload(store, payload):
    with pytest.raises(ControlError):
        store.create("key", payload)
    assert store.get("key") is None


def test_depth_and_duplicate_batch_rejected(store):
    deep = {}
    for _ in range(18):
        deep = {"x": deep}
    with pytest.raises(ControlError):
        store.create("deep", deep)
    with pytest.raises(ControlError):
        store.batch([ControlMutation("one", {}), ControlMutation("one", {})])
    assert store.get("one") is None


@pytest.mark.parametrize("revision", [None, True, 0, -1, 2**53])
def test_invalid_revision_cannot_create(store, revision):
    with pytest.raises(ControlError):
        store.update("new", {}, revision)
    assert store.get("new") is None


@pytest.mark.parametrize(
    "tamper",
    [
        "PRAGMA user_version=2",
        "PRAGMA application_id=0",
        "CREATE TABLE unknown (value TEXT)",
        "CREATE TRIGGER injected AFTER INSERT ON records BEGIN DELETE FROM records; END",
    ],
)
def test_schema_tampering_fails_closed(store, tamper):
    with sqlite3.connect(store.path) as db:
        db.execute(tamper)
    with pytest.raises(ControlError):
        ControlStore(store.directory)
    with pytest.raises(ControlError):
        store.create("record", {})


@pytest.mark.parametrize("payload", ['{"x":1,"x":2}', '{"x":NaN}', "[" * 2000, "[]"])
def test_corrupt_json_record_rejected(store, payload):
    store.create("record", {})
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE records SET payload=?", (payload,))
    with pytest.raises(ControlError):
        store.get("record")
    with pytest.raises(ControlError):
        store.list()


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode and FIFO tests")
def test_permissions_symlink_and_special_file_fail_closed(tmp_path):
    directory = tmp_path.resolve() / "control"
    store = ControlStore(directory)
    assert directory.stat().st_mode & 0o777 == 0o700
    assert store.path.stat().st_mode & 0o777 == 0o600
    store.path.chmod(0o644)
    with pytest.raises(ControlError):
        store.get("key")
    store.path.unlink()
    outside = tmp_path.resolve() / "outside"
    outside.write_text("private")
    store.path.symlink_to(outside)
    with pytest.raises(ControlError):
        ControlStore(directory)
    store.path.unlink()
    os.mkfifo(store.path, 0o600)
    with pytest.raises(ControlError):
        ControlStore(directory)
    assert outside.read_text() == "private"


def test_control_error_contains_no_payload_or_path(store):
    with pytest.raises(ControlError) as caught:
        store.create("INVALID?", {"secret": "sensitive"})
    assert str(caught.value) == "Protected sync control operation failed"
    assert str(store.path) not in str(caught.value)
