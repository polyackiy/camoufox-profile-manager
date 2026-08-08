"""
Pure, platform-independent parts of Chrome's App-Bound Encryption (ABE).

App-Bound Encryption was introduced in Chrome 127 (July 2024) on Windows to make
cookie theft harder for infostealer malware. Cookies written by an ABE-enabled
Chrome carry a ``v20`` prefix and can no longer be decrypted with the classic
per-user DPAPI key that ``cookie_decryptor.py`` handles for ``v10``/``v11``.

The ``v20`` key material lives in ``Local State`` under
``os_crypt.app_bound_encrypted_key`` and is protected by two nested DPAPI layers
(SYSTEM context on the outside, user context on the inside) plus an inner
AEAD wrapping performed by Chrome's SYSTEM-level elevation service. Removing the
DPAPI layers requires Windows syscalls and, for the outer SYSTEM layer,
SYSTEM-level access; those steps live in the guarded ``abe_windows`` module.

Everything that does *not* need a Windows syscall lives here so it can be unit
tested on any platform with synthetic fixtures: parsing the key blob, unwrapping
the master key with the elevation service's AEAD keys, and decrypting an
individual ``v20`` cookie value.

References (format documented by public reverse-engineering, 2024-2025):
- runassu, "Chrome COOKIE v20 decryption PoC":
  https://github.com/runassu/chrome_v20_decryption
- CyberArk, "C4 Bomb: Blowing Up Chrome's AppBound Cookie Encryption":
  https://www.cyberark.com/resources/threat-research-blog/c4-bomb-blowing-up-chromes-appbound-cookie-encryption
- Google Security Blog, "Improving the security of Chrome cookies on Windows":
  https://security.googleblog.com/2024/07/improving-security-of-chrome-cookies-on.html

The keys below are *decryption* constants extracted from Chrome's own
``elevation_service.exe`` by the public research above. They are not secrets in
any meaningful sense: they ship inside every Chrome install. They exist only so a
profile's *own* cookies can be read back, which is the sole purpose of this
migration extra.
"""

from __future__ import annotations

import base64
import struct
from collections.abc import Callable
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305

from .cookie_crypto import CookieDecryptionError, strip_domain_prefix

# Marker at the front of the base64-decoded ``app_bound_encrypted_key``. It is a
# tag Chrome writes, not part of the DPAPI blob, so it is stripped before the
# first unprotect. See runassu PoC ``main()``.
APP_BOUND_KEY_PREFIX = b"APPB"

# Version tag on individual cookie ``encrypted_value`` blobs written under ABE.
V20_COOKIE_PREFIX = b"v20"

# The AEAD used both for the inner key wrap and the final cookie both use a
# 12-byte nonce and a 16-byte tag (GCM / Poly1305 defaults).
_AEAD_NONCE_LEN = 12
_AEAD_TAG_LEN = 16

# The wrapped master key and, for flag 3, the CNG-wrapped inner key, are always
# 32 bytes of ciphertext (a 256-bit key).
_WRAPPED_KEY_CIPHERTEXT_LEN = 32

# Every ``v20`` cookie plaintext is prefixed by a 32-byte domain-bound hash that
# Chrome prepends before encrypting; the real cookie value follows it. This is
# the ABE analogue of the SHA-256 domain prefix Chrome already used for v10.
_COOKIE_PLAINTEXT_PREFIX_LEN = 32

# AEAD keys hardcoded in ``elevation_service.exe``, keyed by the flag byte that
# begins the decrypted key blob. Chrome has shipped three inner schemes; the
# flag byte selects which. Values from the runassu PoC ``derive_v20_master_key``.
#
# - flag 1: AES-256-GCM with a fixed key.
# - flag 2: ChaCha20-Poly1305 with a fixed key.
# - flag 3: the wrapped key is first unwrapped by the machine's CNG key
#   ("Google Chromekey1", SYSTEM-only), then XOR-ed with the constant below, and
#   the result is the AES-256-GCM key. Only the XOR constant is fixed; the CNG
#   step is machine-bound and lives in ``abe_windows``.
_FLAG1_AES_KEY = bytes.fromhex("B31C6E241AC846728DA9C1FAC4936651CFFB944D143AB816276BCC6DA0284787")
_FLAG2_CHACHA20_KEY = bytes.fromhex(
    "E98F37D7F4E1FA433D19304DC2258042090E2D1D7EEA7670D41F738D08729660"
)
_FLAG3_XOR_KEY = bytes.fromhex("CCF8A1CEC56605B8517552BA1A2D061C03A29E90274FB2FCF59BA4B75C392390")


class V20DecryptionError(Exception):
    """Raised when a ``v20`` key blob or cookie cannot be decrypted.

    Callers should treat this as "cannot decrypt" and skip the cookie rather than
    write a partially decrypted or garbage value.
    """


@dataclass
class ParsedKeyBlob:
    """The result of parsing the twice-DPAPI-unwrapped ``app_bound_encrypted_key``.

    ``header`` is the validation data (a normalized Chrome path the elevation
    service checks against the caller); this migration code does not enforce it.
    ``flag`` selects the inner AEAD scheme. ``encrypted_aes_key`` is present only
    for flag 3, where it is the CNG-wrapped inner key.
    """

    header: bytes
    flag: int
    nonce: bytes
    ciphertext: bytes
    tag: bytes
    encrypted_aes_key: bytes | None = None


def decode_app_bound_key(app_bound_encrypted_key_b64: str) -> bytes:
    """Base64-decode ``os_crypt.app_bound_encrypted_key`` and strip the ``APPB`` tag.

    The returned bytes are the still-DPAPI-wrapped key blob, ready for the SYSTEM
    then user DPAPI unwrap performed by ``abe_windows``.
    """
    raw = base64.b64decode(app_bound_encrypted_key_b64)
    if raw[: len(APP_BOUND_KEY_PREFIX)] != APP_BOUND_KEY_PREFIX:
        raise V20DecryptionError(
            "app_bound_encrypted_key does not start with the expected 'APPB' tag; "
            "the Local State format may have changed."
        )
    return raw[len(APP_BOUND_KEY_PREFIX) :]


def parse_key_blob(blob: bytes) -> ParsedKeyBlob:
    """Parse the plaintext key blob produced by the double DPAPI unwrap.

    Layout (little-endian lengths), from the runassu PoC ``parse_key_blob``::

        [u32 header_len][header][u32 content_len][flag]<flag-specific body>

    flag 1/2 body: ``[nonce:12][ciphertext:32][tag:16]``
    flag 3 body:   ``[encrypted_aes_key:32][nonce:12][ciphertext:32][tag:16]``
    """
    try:
        offset = 0
        (header_len,) = struct.unpack_from("<I", blob, offset)
        offset += 4
        header = blob[offset : offset + header_len]
        offset += header_len
        (content_len,) = struct.unpack_from("<I", blob, offset)
        offset += 4
    except struct.error as exc:
        raise V20DecryptionError(f"key blob is too short to parse: {exc}") from exc

    # The PoC asserts the two lengths plus the two u32 fields account for the
    # whole blob; a mismatch means we are not looking at an ABE key blob.
    if header_len + content_len + 8 != len(blob):
        raise V20DecryptionError(
            "key blob length does not match its header/content lengths; unexpected format."
        )

    if offset >= len(blob):
        raise V20DecryptionError("key blob has no flag byte.")
    flag = blob[offset]
    offset += 1

    encrypted_aes_key: bytes | None = None
    if flag == 3:
        encrypted_aes_key = blob[offset : offset + _WRAPPED_KEY_CIPHERTEXT_LEN]
        offset += _WRAPPED_KEY_CIPHERTEXT_LEN
        if len(encrypted_aes_key) != _WRAPPED_KEY_CIPHERTEXT_LEN:
            raise V20DecryptionError("key blob truncated before the CNG-wrapped key.")
    elif flag not in (1, 2):
        raise V20DecryptionError(f"unsupported ABE key flag: {flag}")

    nonce = blob[offset : offset + _AEAD_NONCE_LEN]
    offset += _AEAD_NONCE_LEN
    ciphertext = blob[offset : offset + _WRAPPED_KEY_CIPHERTEXT_LEN]
    offset += _WRAPPED_KEY_CIPHERTEXT_LEN
    tag = blob[offset : offset + _AEAD_TAG_LEN]
    offset += _AEAD_TAG_LEN

    if (
        len(nonce) != _AEAD_NONCE_LEN
        or len(ciphertext) != _WRAPPED_KEY_CIPHERTEXT_LEN
        or len(tag) != _AEAD_TAG_LEN
    ):
        raise V20DecryptionError("key blob truncated before the wrapped master key.")

    return ParsedKeyBlob(
        header=header,
        flag=flag,
        nonce=nonce,
        ciphertext=ciphertext,
        tag=tag,
        encrypted_aes_key=encrypted_aes_key,
    )


def _xor_bytes(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b, strict=True))


def unwrap_master_key(
    parsed: ParsedKeyBlob,
    cng_decrypt: Callable[[bytes], bytes] | None = None,
) -> bytes:
    """Recover the 32-byte AES-256-GCM master key from a parsed key blob.

    Flags 1 and 2 unwrap with a fixed AEAD key and are fully computable here.
    Flag 3 additionally needs the machine's SYSTEM-only CNG key to unwrap
    ``encrypted_aes_key``; the caller injects that as ``cng_decrypt`` (provided by
    ``abe_windows`` on Windows). Injecting the CNG step keeps this function pure
    and unit-testable while still supporting flag 3 in production.
    """
    if parsed.flag == 1:
        cipher: AESGCM | ChaCha20Poly1305 = AESGCM(_FLAG1_AES_KEY)
    elif parsed.flag == 2:
        cipher = ChaCha20Poly1305(_FLAG2_CHACHA20_KEY)
    elif parsed.flag == 3:
        if cng_decrypt is None or parsed.encrypted_aes_key is None:
            raise V20DecryptionError(
                "flag 3 key requires the machine's SYSTEM-bound CNG key "
                "(Google Chromekey1), which is only available when running "
                "elevated on the originating Windows machine."
            )
        # The CNG-unwrapped key is XOR-ed with a fixed constant before use; both
        # steps are required to reconstruct the AES-256-GCM key. See runassu PoC.
        derived = _xor_bytes(cng_decrypt(parsed.encrypted_aes_key), _FLAG3_XOR_KEY)
        cipher = AESGCM(derived)
    else:
        raise V20DecryptionError(f"unsupported ABE key flag: {parsed.flag}")

    try:
        # cryptography's AEAD API takes ciphertext||tag concatenated.
        return cipher.decrypt(parsed.nonce, parsed.ciphertext + parsed.tag, None)
    except Exception as exc:  # InvalidTag and friends
        raise V20DecryptionError(f"failed to unwrap the ABE master key: {exc}") from exc


def decrypt_v20_cookie_value(
    encrypted_value: bytes, master_key: bytes, host_key: str | None = None
) -> str:
    """Decrypt one ``v20`` cookie ``encrypted_value`` with the ABE master key.

    Layout: ``[b"v20"][nonce:12][ciphertext:var][tag:16]``, AES-256-GCM.

    ``host_key`` is the cookie's domain, passed only for cookie stores at schema
    24 or later, where the plaintext begins with ``SHA256(host_key)``. That
    prefix belongs to the cookie store rather than to App-Bound Encryption, so
    Chrome 127-129 could write ``v20`` cookies into a schema-23 database that has
    no prefix at all — stripping unconditionally, as this used to, would take 32
    bytes off the front of those values.
    """
    if encrypted_value[: len(V20_COOKIE_PREFIX)] != V20_COOKIE_PREFIX:
        raise V20DecryptionError("cookie value is not v20-prefixed.")

    body = encrypted_value[len(V20_COOKIE_PREFIX) :]
    if len(body) < _AEAD_NONCE_LEN + _AEAD_TAG_LEN:
        raise V20DecryptionError("v20 cookie value is too short.")

    nonce = body[:_AEAD_NONCE_LEN]
    ciphertext_and_tag = body[_AEAD_NONCE_LEN:]

    try:
        plaintext = AESGCM(master_key).decrypt(nonce, ciphertext_and_tag, None)
    except Exception as exc:  # InvalidTag and friends
        raise V20DecryptionError(f"failed to decrypt v20 cookie: {exc}") from exc

    if host_key is not None:
        try:
            plaintext = strip_domain_prefix(plaintext, host_key)
        except CookieDecryptionError as exc:
            raise V20DecryptionError(str(exc)) from exc

    # Strict, like the v10/v11 path: the GCM tag has already proved the plaintext
    # is genuine, so bytes that are not UTF-8 mean the value is not text — better
    # skipped than written with the bad characters silently dropped.
    try:
        return plaintext.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise V20DecryptionError(f"decrypted v20 cookie is not valid UTF-8: {exc}") from exc
