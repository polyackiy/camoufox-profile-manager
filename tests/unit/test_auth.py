"""Password hashing and login-session logic."""

from datetime import datetime, timedelta

import pytest

from camoufox_pm.core import auth


def test_password_hash_is_argon2id_and_never_the_password():
    hashed = auth.hash_password("correct horse battery staple")
    assert hashed.startswith("$argon2id$")
    assert "correct horse battery staple" not in hashed


def test_password_verifies_and_wrong_password_does_not():
    hashed = auth.hash_password("right-password")
    assert auth.verify_password(hashed, "right-password")
    assert not auth.verify_password(hashed, "wrong-password")


def test_same_password_hashes_differently_each_time():
    """A random salt per hash, so equal passwords are not linkable in the table."""
    assert auth.hash_password("pw-123456") != auth.hash_password("pw-123456")


def test_garbage_hash_verifies_false_not_raises():
    assert not auth.verify_password("not-a-hash-at-all", "anything")
    assert not auth.verify_password("", "anything")


def test_dummy_verify_swallows_the_mismatch():
    """Burned for unknown usernames; must never raise."""
    auth.verify_dummy("whatever")


def test_token_is_stored_only_as_its_hash():
    token = auth.new_session_token()
    hashed = auth.hash_token(token)
    assert hashed != token
    assert token not in hashed
    # Deterministic, so the lookup on the next request finds the row.
    assert auth.hash_token(token) == hashed


async def test_session_roundtrip(storage):
    await storage.create_user("u1", "alice", auth.hash_password("pw-123456"))
    token = await auth.create_session(storage, "u1")

    assert await auth.validate_session(storage, token) == "alice"
    assert await auth.validate_session(storage, "some-other-token") is None


async def test_expired_session_is_rejected_and_removed(storage):
    await storage.create_user("u1", "alice", auth.hash_password("pw-123456"))
    token = auth.new_session_token()
    await storage.create_session(
        auth.hash_token(token), "u1", datetime.now() - timedelta(seconds=1)
    )

    assert await auth.validate_session(storage, token) is None
    # The row is gone, not just ignored.
    assert await storage.get_session(auth.hash_token(token)) is None


async def test_deleting_a_user_invalidates_their_sessions(storage):
    await storage.create_user("u1", "alice", auth.hash_password("pw-123456"))
    token = await auth.create_session(storage, "u1")

    assert await storage.delete_user("alice")
    assert await auth.validate_session(storage, token) is None


async def test_duplicate_username_is_refused_case_insensitively(storage):
    await storage.create_user("u1", "Alice", auth.hash_password("pw-123456"))
    with pytest.raises(ValueError):
        await storage.create_user("u2", "alice", auth.hash_password("pw-123456"))


async def test_expired_sessions_sweep(storage):
    await storage.create_user("u1", "alice", auth.hash_password("pw-123456"))
    live = await auth.create_session(storage, "u1")
    stale = auth.new_session_token()
    await storage.create_session(auth.hash_token(stale), "u1", datetime.now() - timedelta(hours=1))

    assert await storage.delete_expired_sessions() == 1
    assert await auth.validate_session(storage, live) == "alice"
