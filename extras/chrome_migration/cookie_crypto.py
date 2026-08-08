"""How Chrome encrypts a cookie value, per platform.

The ``v10``/``v11`` prefix means different things on different operating systems,
which is the trap this module exists to close:

* **Windows** — AES-256-GCM. Layout ``[v10|v11][nonce:12][ciphertext][tag:16]``.
  The key is the DPAPI-unwrapped ``os_crypt.encrypted_key`` from ``Local State``.
* **macOS and Linux** — AES-128-CBC with an IV of sixteen spaces and PKCS#7
  padding. Layout ``[v10|v11][ciphertext]``. The key is PBKDF2 over a password
  from the Keychain (macOS) or the keyring (Linux); ``v11`` differs from ``v10``
  only in *where that password comes from*, never in the cipher.

Reading the version tag alone and picking one algorithm cannot work. Doing so
decrypted Windows cookies with CBC, which fails outright unless the body happens
to be a multiple of the block size — and then returns plausible-looking rubbish.

Everything here is pure and built on ``cryptography``, a core dependency, so the
whole file is testable on any platform without the ``chrome-migration`` extra.
"""

from __future__ import annotations

import hashlib

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SUPPORTED_PREFIXES = (b"v10", b"v11")

# Cookie-store schema 24 (Chrome ~130, August 2024) started encrypting
# ``SHA256(host_key) || value`` instead of the bare value. It is a change in the
# cookie store, not in any cipher, so it applies to v10, v11 and v20 alike and on
# every platform — which is why abe.py already strips 32 bytes for v20.
DOMAIN_PREFIX_SCHEMA_VERSION = 24
DOMAIN_PREFIX_LEN = 32

# Chrome's own constants for the CBC platforms.
_CBC_IV = b" " * 16
_AEAD_NONCE_LEN = 12
_AEAD_TAG_LEN = 16
_BLOCK = 16


class CookieDecryptionError(Exception):
    """A cookie value could not be decrypted, so it must be skipped."""


def strip_domain_prefix(plaintext: bytes, host_key: str) -> bytes:
    """Remove the ``SHA256(host_key)`` that schema 24 prepends, after checking it.

    Chrome verifies this digest and drops the cookie when it does not match, so we
    do the same rather than trusting the first 32 bytes. It doubles as the
    integrity check CBC otherwise lacks: with a wrong key the padding can still
    validate by chance, but a random 32 bytes will not equal the digest of the
    row's own domain.
    """
    if len(plaintext) < DOMAIN_PREFIX_LEN:
        raise CookieDecryptionError("plaintext is too short to hold the domain digest")

    expected = hashlib.sha256(host_key.encode("utf-8")).digest()
    if plaintext[:DOMAIN_PREFIX_LEN] != expected:
        raise CookieDecryptionError("the domain digest does not match this cookie's host_key")
    return plaintext[DOMAIN_PREFIX_LEN:]


def _strip_prefix(encrypted_value: bytes) -> bytes:
    prefix = encrypted_value[:3]
    if prefix not in SUPPORTED_PREFIXES:
        raise CookieDecryptionError(f"unsupported cookie version {prefix!r}")
    return encrypted_value[3:]


def _decode(plaintext: bytes) -> str:
    """Decode strictly.

    For CBC this is load-bearing: there is no authentication tag, so a wrong key
    yields random bytes and a failed decode is one of the only signals that
    anything went wrong. For GCM the tag has already proved the plaintext is
    genuine, so a failure here means the value simply is not UTF-8 — rare enough
    that skipping it beats writing something mangled.
    """
    try:
        return plaintext.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CookieDecryptionError(f"decrypted bytes are not valid UTF-8: {exc}") from exc


def _decrypt_gcm(encrypted_value: bytes, key: bytes) -> bytes:
    """Windows: AES-256-GCM over ``[nonce:12][ciphertext][tag:16]``."""
    body = _strip_prefix(encrypted_value)
    if len(body) < _AEAD_NONCE_LEN + _AEAD_TAG_LEN:
        raise CookieDecryptionError("cookie value is too short to hold a nonce and a tag")

    nonce, ciphertext_and_tag = body[:_AEAD_NONCE_LEN], body[_AEAD_NONCE_LEN:]
    try:
        return AESGCM(key).decrypt(nonce, ciphertext_and_tag, None)
    except Exception as exc:  # InvalidTag, wrong key length, and friends
        raise CookieDecryptionError(f"AES-GCM decryption failed: {exc}") from exc


def _decrypt_cbc(encrypted_value: bytes, key: bytes) -> bytes:
    """macOS and Linux: AES-128-CBC, IV of sixteen spaces, PKCS#7."""
    body = _strip_prefix(encrypted_value)
    if not body or len(body) % _BLOCK:
        raise CookieDecryptionError("cookie value is not a whole number of AES blocks")

    try:
        decryptor = Cipher(algorithms.AES(key), modes.CBC(_CBC_IV)).decryptor()
        padded = decryptor.update(body) + decryptor.finalize()
    except ValueError as exc:  # a key of the wrong length would pick another AES variant
        raise CookieDecryptionError(f"AES-CBC decryption failed: {exc}") from exc

    # Validate the padding rather than trusting the last byte. With the wrong key
    # the plaintext is random, and stripping "however many bytes the last one
    # says" would silently truncate or corrupt a value that should be skipped.
    # Not constant-time on purpose: this reads a local file once, offline, with
    # no attacker able to observe timing, so there is no padding oracle to open.
    pad = padded[-1]
    if not 1 <= pad <= _BLOCK or padded[-pad:] != bytes([pad]) * pad:
        raise CookieDecryptionError("PKCS#7 padding is invalid; the key is probably wrong")
    return padded[:-pad]


def decrypt_aes_gcm(encrypted_value: bytes, key: bytes, host_key: str | None = None) -> str:
    """Decrypt a Windows ``v10``/``v11`` cookie value."""
    plaintext = _decrypt_gcm(encrypted_value, key)
    return _decode(strip_domain_prefix(plaintext, host_key) if host_key else plaintext)


def decrypt_aes_cbc(encrypted_value: bytes, key: bytes, host_key: str | None = None) -> str:
    """Decrypt a macOS or Linux ``v10``/``v11`` cookie value."""
    plaintext = _decrypt_cbc(encrypted_value, key)
    return _decode(strip_domain_prefix(plaintext, host_key) if host_key else plaintext)


def decrypt_cookie_value(
    encrypted_value: bytes, key: bytes, system: str, host_key: str | None = None
) -> str:
    """Decrypt a ``v10``/``v11`` cookie value for the platform that wrote it.

    ``system`` is ``platform.system().lower()``. Pass ``host_key`` when the cookie
    database is at schema 24 or later, where the plaintext carries a verifiable
    ``SHA256(host_key)`` prefix; omit it for older stores, which do not.

    Unknown systems are refused rather than assumed to be CBC: under Cygwin or
    MSYS ``platform.system()`` reports something like ``cygwin_nt-10.0``, and
    quietly treating a Windows profile as CBC would skip every cookie it has.
    """
    if system == "windows":
        return decrypt_aes_gcm(encrypted_value, key, host_key)
    if system in ("darwin", "linux"):
        return decrypt_aes_cbc(encrypted_value, key, host_key)
    raise CookieDecryptionError(f"no known cookie cipher for platform {system!r}")
