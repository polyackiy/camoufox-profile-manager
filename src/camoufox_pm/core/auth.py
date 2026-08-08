"""User accounts and login sessions.

Passwords are hashed with argon2id. Sessions are opaque random tokens stored
server-side: only the SHA-256 of a token ever touches the database, so a copy
of the database does not contain anything a browser could replay, and deleting
the row is a real logout — nothing signed stays valid after it.
"""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from camoufox_pm.config import get_settings

SESSION_COOKIE = "cpm_session"

MIN_PASSWORD_LENGTH = 8

_hasher = PasswordHasher()

# Verified against when the username does not exist, so a login attempt costs
# the same whether or not the account is real.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    """Hash ``password`` with argon2id (salt and parameters travel in the hash)."""
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Check ``password`` against a stored hash; malformed hashes verify false."""
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def verify_dummy(password: str) -> None:
    """Burn the same argon2 work as a real check, for unknown usernames."""
    try:
        _hasher.verify(_DUMMY_HASH, password)
    except VerificationError:
        pass


def password_needs_rehash(password_hash: str) -> bool:
    """Whether the stored hash predates the current argon2 parameters."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return False


def new_user_id() -> str:
    return uuid.uuid4().hex


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """The database stores this, never the token itself."""
    return hashlib.sha256(token.encode()).hexdigest()


async def create_session(storage, user_id: str) -> str:
    """Open a session for ``user_id`` and return the token for the cookie."""
    token = new_session_token()
    expires_at = datetime.now() + timedelta(hours=get_settings().session_ttl_hours)
    await storage.create_session(hash_token(token), user_id, expires_at)
    return token


async def validate_session(storage, token: str) -> str | None:
    """Return the username the token belongs to, or None. Expired rows are removed."""
    session = await storage.get_session(hash_token(token))
    if session is None:
        return None
    if datetime.fromisoformat(session["expires_at"]) <= datetime.now():
        await storage.delete_session(session["token_hash"])
        return None
    return session["username"]
