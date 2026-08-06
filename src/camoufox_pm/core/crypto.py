"""Symmetric encryption for secrets at rest (proxy credentials).

Uses Fernet keyed by ``CPM_SECRET_KEY``. When no key is configured the functions
are the identity transform, so local-only setups keep working. Decryption also
tolerates plaintext values written before a key was configured, easing migration.
"""

from cryptography.fernet import Fernet, InvalidToken

from camoufox_pm.config import get_settings

_PREFIX = "enc:"


def _fernet() -> Fernet | None:
    key = get_settings().secret_key
    if not key:
        return None
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(value: str) -> str:
    """Encrypt ``value``. Returns it unchanged when no key is configured."""
    fernet = _fernet()
    if fernet is None:
        return value
    token = fernet.encrypt(value.encode()).decode()
    return _PREFIX + token


def decrypt(value: str) -> str:
    """Decrypt ``value``. Plaintext and unkeyed values pass through unchanged."""
    fernet = _fernet()
    if fernet is None or not value.startswith(_PREFIX):
        return value
    try:
        return fernet.decrypt(value[len(_PREFIX) :].encode()).decode()
    except InvalidToken:
        return value
