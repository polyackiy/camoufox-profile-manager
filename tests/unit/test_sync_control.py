"""Cross-process CAS, journal recovery and fail-closed control storage tests."""

import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
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
    try:
        return ControlStore(tmp_path.resolve() / "control")
    except ControlError as exc:
        cause = exc
        while cause.__context__ is not None:
            cause = cause.__context__
        raise AssertionError("Synthetic control fixture initialization failed") from cause


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
    with closing(sqlite3.connect(store.path)) as db, db:
        db.execute(tamper)
    with pytest.raises(ControlError):
        ControlStore(store.directory)
    with pytest.raises(ControlError):
        store.create("record", {})


@pytest.mark.parametrize("payload", ['{"x":1,"x":2}', '{"x":NaN}', "[" * 2000, "[]"])
def test_corrupt_json_record_rejected(store, payload):
    store.create("record", {})
    with closing(sqlite3.connect(store.path)) as db, db:
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


def test_optional_journal_can_disappear_during_native_acl_check(store, monkeypatch):
    from camoufox_pm.core.update_permissions import create_private_file
    from camoufox_pm.sync import control

    store.create("record", {"public": True})
    journal = store.path.with_name(store.path.name + "-journal")
    journal.unlink(missing_ok=True)
    os.close(create_private_file(journal))
    original = control.verify_private_path

    def disappear(path, **kwargs):
        if path == journal and path.exists():
            path.unlink()
        return original(path, **kwargs)

    monkeypatch.setattr(control, "verify_private_path", disappear)
    assert store.get("record").payload == {"public": True}


def test_existing_unsafe_journal_still_fails_closed(store, monkeypatch):
    from camoufox_pm.core.update_permissions import PermissionProtectionError, create_private_file
    from camoufox_pm.sync import control

    journal = store.path.with_name(store.path.name + "-journal")
    journal.unlink(missing_ok=True)
    os.close(create_private_file(journal))
    original = control.verify_private_path

    def reject(path, **kwargs):
        if path == journal:
            raise PermissionProtectionError("unsafe synthetic ACL")
        return original(path, **kwargs)

    monkeypatch.setattr(control, "verify_private_path", reject)
    with pytest.raises(ControlError):
        store.get("record")
    assert journal.exists()


def test_optional_journal_can_disappear_during_reparse_walk(store, monkeypatch):
    from camoufox_pm.core.update_permissions import create_private_file
    from camoufox_pm.sync import control

    store.create("record", {"public": True})
    journal = store.path.with_name(store.path.name + "-journal")
    journal.unlink(missing_ok=True)
    os.close(create_private_file(journal))
    original = control.reject_reparse_paths

    def disappear(path):
        if path == journal and path.exists():
            path.unlink()
            raise FileNotFoundError("synthetic concurrent commit")
        return original(path)

    monkeypatch.setattr(control, "reject_reparse_paths", disappear)
    assert store.get("record").payload == {"public": True}


def test_main_database_disappearance_is_never_optional(store, monkeypatch):
    from camoufox_pm.sync import control

    original = control.reject_reparse_paths

    def disappear(path):
        if path == store.path:
            raise FileNotFoundError("synthetic missing main database")
        return original(path)

    monkeypatch.setattr(control, "reject_reparse_paths", disappear)
    with pytest.raises(ControlError):
        store.get("record")


def test_concurrent_store_inspection_and_commits(store):
    """Native ACL/path checks must coexist with SQLite's journal lifecycle."""
    import threading

    store.create("counter", {"value": 0})
    start = threading.Barrier(2)

    def guarded(operation):
        start.wait(timeout=10)
        try:
            operation()
        except ControlError as error:
            # This fixture contains only synthetic public counters. Preserve the
            # original native failure for diagnosis instead of sanitized API text.
            cause = error
            for _ in range(8):
                following = cause.__cause__ or cause.__context__
                if following is None or following is cause:
                    break
                cause = following
            raise AssertionError("Concurrent control access failed") from cause

    def writer():
        for value in range(1, 81):
            store.update("counter", {"value": value}, value)

    def reader():
        for _ in range(80):
            reopened = ControlStore(store.directory)
            assert reopened.get("counter").payload["value"] >= 0
            assert len(reopened.list()) == 1

    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(guarded, operation) for operation in (writer, reader)]
        for job in jobs:
            job.result(timeout=90)
    assert store.get("counter").payload == {"value": 80}


def test_control_journal_retains_private_identity_across_commits(store):
    from camoufox_pm.core.update_permissions import verify_private_path

    journal = store.path.with_name(store.path.name + "-journal")
    identity = journal.stat().st_ino
    record = store.create("record", {"value": 1})
    for value in range(2, 6):
        record = store.update("record", {"value": value}, record.revision)
        assert journal.stat().st_ino == identity
        assert journal.stat().st_size == 0
        verify_private_path(journal)
    assert ControlStore(store.directory).get("record") == record


def test_concurrent_first_creation_waits_for_initializer(tmp_path, monkeypatch):
    import threading

    from camoufox_pm.sync import control

    directory = tmp_path.resolve() / "fresh-control"
    created, release = threading.Event(), threading.Event()
    original_create = control.create_private_file
    original_schema = ControlStore._schema
    original_try_lock = getattr(control, "_try_initialization_lock", None)
    premature_reads = []

    def create(path):
        fd = original_create(path)
        if path.name == "control.sqlite3":
            created.set()
            if not release.wait(timeout=10):
                os.close(fd)
                raise AssertionError("Initializer test watchdog expired")
        return fd

    def schema(db):
        if db.execute("PRAGMA user_version").fetchone()[0] == 0:
            premature_reads.append(True)
            release.set()
        return original_schema(db)

    def try_lock(handle):
        try:
            original_try_lock(handle)
        except OSError:
            # The second opener reached real lock contention, not a sleep-based
            # scheduling guess. Let the first opener finish its private schema.
            release.set()
            raise

    monkeypatch.setattr(control, "create_private_file", create)
    monkeypatch.setattr(ControlStore, "_schema", staticmethod(schema))
    monkeypatch.setattr(control, "_try_initialization_lock", try_lock, raising=False)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(ControlStore, directory)
        try:
            assert created.wait(timeout=5)
            second = pool.submit(ControlStore, directory)
            right = second.result(timeout=15)
        finally:
            release.set()
        left = first.result(timeout=15)
    assert premature_reads == []
    assert left.path == right.path and left.list() == right.list() == ()
    left.create("initialized", {"value": 1})
    assert right.get("initialized").payload == {"value": 1}


def test_existing_empty_control_database_is_not_adopted(tmp_path):
    from camoufox_pm.core.update_permissions import create_private_directory, create_private_file

    directory = tmp_path.resolve() / "preexisting-empty"
    create_private_directory(directory)
    database = directory / "control.sqlite3"
    os.close(create_private_file(database))
    with pytest.raises(ControlError):
        ControlStore(directory)
    with closing(sqlite3.connect(database)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 0
        assert db.execute("SELECT name FROM sqlite_master").fetchall() == []


def test_initialization_lock_timeout_closes_waiter_descriptor(store, monkeypatch):
    from camoufox_pm.sync import control

    descriptors = []
    original_open = control.os.open

    def opened(path, *args, **kwargs):
        descriptor = original_open(path, *args, **kwargs)
        if str(path).endswith("control.init.lock"):
            descriptors.append(descriptor)
        return descriptor

    monkeypatch.setattr(control.os, "open", opened)
    with control._initialization_lock(store.directory):
        monkeypatch.setattr(control, "_INITIALIZATION_WAIT_SECONDS", 0)
        with pytest.raises(ControlError):
            ControlStore(store.directory)
        assert len(descriptors) == 2
        with pytest.raises(OSError):
            os.fstat(descriptors[-1])
        assert os.fstat(descriptors[0]).st_nlink == 1
    assert ControlStore(store.directory).list() == ()


@pytest.mark.skipif(os.name == "nt", reason="POSIX private init-lock path controls")
@pytest.mark.parametrize("kind", ["symlink", "hardlink", "public_permissions"])
def test_unsafe_initialization_lock_is_rejected(store, kind):
    from camoufox_pm.core.update_permissions import create_private_file

    lock = store.directory / "control.init.lock"
    lock.unlink()
    target = store.directory / "private-init-control"
    os.close(create_private_file(target))
    if kind == "symlink":
        lock.symlink_to(target)
    elif kind == "hardlink":
        os.link(target, lock)
    else:
        os.close(create_private_file(lock))
        lock.chmod(0o644)
    with pytest.raises(ControlError):
        ControlStore(store.directory)
    assert store.path.is_file()


def test_initialization_lock_releases_after_process_kill(store):
    import time

    signal = store.directory / "child-lock-ready"
    script = """
import sys
from pathlib import Path
from camoufox_pm.sync.control import _initialization_lock
with _initialization_lock(Path(sys.argv[1])):
    Path(sys.argv[2]).write_text("ready")
    sys.stdin.read()
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(store.directory), str(signal)],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 10
        while not signal.exists():
            assert process.poll() is None and time.monotonic() < deadline
            time.sleep(0.01)
        process.kill()
        process.wait(timeout=10)
        assert ControlStore(store.directory).list() == ()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        process.stdin.close()


@pytest.mark.parametrize("unsafe", [False, True])
def test_native_existing_directory_race_requires_verified_private_owner(
    tmp_path, monkeypatch, unsafe
):
    from camoufox_pm.core.update_permissions import PermissionProtectionError
    from camoufox_pm.sync import control

    directory = tmp_path.resolve() / "native-directory-race"
    original = control.create_private_directory

    def raced(path):
        if unsafe:
            # The candidate cannot be accepted as a directory at all.
            path.write_bytes(b"untrusted existing file")
        else:
            original(path)
        raise PermissionProtectionError("Synthetic native already-exists result")

    monkeypatch.setattr(control, "create_private_directory", raced)
    if unsafe:
        with pytest.raises(ControlError):
            ControlStore(directory)
        assert directory.read_bytes() == b"untrusted existing file"
    else:
        assert ControlStore(directory).list() == ()
