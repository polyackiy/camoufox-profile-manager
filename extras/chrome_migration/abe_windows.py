"""
Windows-only syscalls for Chrome App-Bound Encryption (ABE).

This module isolates every step of the ``v20`` key recovery that needs a Windows
API: the two DPAPI unwraps and, for flag-3 keys, the CNG unwrap of the inner
key. It is imported lazily by ``cookie_decryptor`` and guarded so importing it on
macOS or Linux never fails — the syscalls are resolved only inside the functions,
exactly as ``cookie_decryptor`` already does for ``win32crypt``.

Why this is inherently Windows-and-elevation bound (and therefore not runnable in
this project's CI, which is macOS/Linux):

- The ``app_bound_encrypted_key`` is wrapped by DPAPI twice. The *inner* layer is
  the user's own DPAPI and unwraps in the user's context. The *outer* layer is
  SYSTEM-context DPAPI; unwrapping it requires acting as SYSTEM, which Chrome's
  design intends only its SYSTEM-level elevation service to do. The accepted
  offline technique is to impersonate ``lsass.exe`` (needs ``SeDebugPrivilege``,
  i.e. an elevated process) to obtain a SYSTEM token for that one unprotect call.
- Flag-3 keys add a machine-bound CNG key ("Google Chromekey1" in the
  "Microsoft Software Key Storage Provider"), also SYSTEM-only.

This is why full ``v20`` decryption is only offered when the tool runs elevated
on the machine that wrote the cookies. When it cannot run (non-Windows, not
elevated, missing pywin32), the caller degrades honestly and skips ``v20``
cookies instead of writing garbage.

The technique follows the public reverse-engineering write-ups referenced in
``abe.py``. It is deliberately kept behind an explicit call and is never exercised
implicitly.
"""

from __future__ import annotations

import ctypes
import json
import platform
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from loguru import logger

from .abe import (
    ParsedKeyBlob,
    decode_app_bound_key,
    parse_key_blob,
    unwrap_master_key,
)


def is_windows() -> bool:
    return platform.system().lower() == "windows"


def is_elevated() -> bool:
    """Best-effort admin check; returns False off Windows or on any error."""
    if not is_windows():
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except Exception:
        return False


@contextmanager
def _impersonate_system() -> Iterator[None]:
    """Impersonate ``lsass.exe`` so the current thread runs as SYSTEM.

    Needed only for the outer, SYSTEM-context DPAPI layer and for the flag-3 CNG
    key. Requires an elevated process (for ``SeDebugPrivilege``). Implemented with
    the ``pywin32`` token APIs. The original thread token is always restored.
    """
    import win32api
    import win32con
    import win32security

    # Enable SeDebugPrivilege on our own process so we may open lsass.
    proc_token = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(),
        win32con.TOKEN_ADJUST_PRIVILEGES | win32con.TOKEN_QUERY,
    )
    luid = win32security.LookupPrivilegeValue(None, win32security.SE_DEBUG_NAME)
    win32security.AdjustTokenPrivileges(proc_token, False, [(luid, win32con.SE_PRIVILEGE_ENABLED)])

    lsass_pid = _find_lsass_pid()
    lsass_handle = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION, False, lsass_pid)
    try:
        lsass_token = win32security.OpenProcessToken(
            lsass_handle, win32con.TOKEN_DUPLICATE | win32con.TOKEN_QUERY
        )
        dup = win32security.DuplicateTokenEx(
            lsass_token,
            win32security.SecurityImpersonation,
            win32con.TOKEN_QUERY | win32con.TOKEN_IMPERSONATE,
            win32security.TokenImpersonation,
        )
        win32security.SetThreadToken(None, dup)
        try:
            yield
        finally:
            win32security.SetThreadToken(None, None)
    finally:
        win32api.CloseHandle(lsass_handle)


def _find_lsass_pid() -> int:
    import win32process

    for pid in win32process.EnumProcesses():
        try:
            handle = _open_for_name(pid)
            if handle is None:
                continue
            try:
                name = _process_image_name(handle)
            finally:
                import win32api

                win32api.CloseHandle(handle)
            if name and Path(name).name.lower() == "lsass.exe":
                return pid
        except Exception:
            continue
    raise RuntimeError("could not locate lsass.exe")


def _open_for_name(pid: int):
    import win32api
    import win32con

    try:
        return win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    except Exception:
        return None


def _process_image_name(handle) -> str | None:
    import win32process

    try:
        return win32process.GetModuleFileNameEx(handle, 0)
    except Exception:
        return None


def _dpapi_unprotect(blob: bytes) -> bytes:
    """Remove one DPAPI layer in the current (thread) security context."""
    import win32crypt

    return win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1]


def _cng_decrypt(encrypted_key: bytes) -> bytes:
    """Unwrap a flag-3 inner key with the machine's SYSTEM-bound CNG key.

    Opens "Google Chromekey1" in the "Microsoft Software Key Storage Provider"
    and calls ``NCryptDecrypt``. Must run in a SYSTEM context. Uses raw ``ctypes``
    against ``ncrypt.dll`` because ``pywin32`` does not wrap these calls; the
    two-call size-then-fill pattern follows the runassu PoC ``decrypt_with_cng``.
    """
    ncrypt = ctypes.windll.NCRYPT  # type: ignore[attr-defined]
    NCRYPT_SILENT_FLAG = 0x40

    h_provider = ctypes.c_void_p()
    status = ncrypt.NCryptOpenStorageProvider(
        ctypes.byref(h_provider), "Microsoft Software Key Storage Provider", 0
    )
    if status != 0:
        raise RuntimeError(f"NCryptOpenStorageProvider failed: {status:#x}")

    try:
        h_key = ctypes.c_void_p()
        status = ncrypt.NCryptOpenKey(h_provider, ctypes.byref(h_key), "Google Chromekey1", 0, 0)
        if status != 0:
            raise RuntimeError(f"NCryptOpenKey failed: {status:#x}")

        try:
            in_buf = (ctypes.c_ubyte * len(encrypted_key)).from_buffer_copy(encrypted_key)
            out_len = ctypes.c_ulong(0)
            status = ncrypt.NCryptDecrypt(
                h_key,
                in_buf,
                len(in_buf),
                None,
                None,
                0,
                ctypes.byref(out_len),
                NCRYPT_SILENT_FLAG,
            )
            if status != 0:
                raise RuntimeError(f"NCryptDecrypt (size) failed: {status:#x}")

            out_buf = (ctypes.c_ubyte * out_len.value)()
            status = ncrypt.NCryptDecrypt(
                h_key,
                in_buf,
                len(in_buf),
                None,
                out_buf,
                out_len.value,
                ctypes.byref(out_len),
                NCRYPT_SILENT_FLAG,
            )
            if status != 0:
                raise RuntimeError(f"NCryptDecrypt failed: {status:#x}")

            return bytes(out_buf[: out_len.value])
        finally:
            ncrypt.NCryptFreeObject(h_key)
    finally:
        ncrypt.NCryptFreeObject(h_provider)


def _unwrap_key_blob(app_bound_encrypted_key_b64: str) -> ParsedKeyBlob:
    """Base64-decode, double-DPAPI-unwrap and parse the app-bound key blob."""
    wrapped = decode_app_bound_key(app_bound_encrypted_key_b64)
    # Outer layer: SYSTEM DPAPI (impersonate lsass). Inner layer: user DPAPI.
    with _impersonate_system():
        system_unwrapped = _dpapi_unprotect(wrapped)
    user_unwrapped = _dpapi_unprotect(system_unwrapped)
    return parse_key_blob(user_unwrapped)


def get_v20_master_key(local_state_path: str | Path) -> bytes | None:
    """Recover the ABE master key from a Chrome ``Local State`` file.

    Returns the 32-byte AES-256-GCM master key, or ``None`` if ABE is not present
    or the environment cannot perform the unwrap (non-Windows, not elevated,
    missing pywin32, or a flag-3 key on a foreign machine). Never raises for the
    "cannot decrypt" case: the caller degrades by skipping ``v20`` cookies.
    """
    if not is_windows():
        logger.debug("ABE key recovery skipped: not running on Windows.")
        return None

    try:
        with open(local_state_path, encoding="utf-8") as f:
            local_state = json.load(f)
    except Exception as exc:
        logger.warning(f"Could not read Local State for ABE key: {exc}")
        return None

    app_bound_key = local_state.get("os_crypt", {}).get("app_bound_encrypted_key")
    if not app_bound_key:
        logger.debug("No app_bound_encrypted_key in Local State; ABE not in use.")
        return None

    if not is_elevated():
        logger.warning(
            "Chrome cookies use App-Bound Encryption (v20), which requires running "
            "this tool as Administrator on the same Windows machine to decrypt. "
            "Skipping v20 cookies. Re-run elevated to migrate them."
        )
        return None

    try:
        parsed = _unwrap_key_blob(app_bound_key)
    except ImportError:
        logger.error(
            "App-Bound Encryption support needs pywin32. Install the extra: "
            "'uv sync --extra chrome-migration'. Skipping v20 cookies."
        )
        return None
    except Exception as exc:
        logger.warning(f"Could not unwrap the ABE key blob: {exc}. Skipping v20 cookies.")
        return None

    try:
        # Flag 3 needs the machine's CNG key, itself SYSTEM-bound.
        cng = None
        if parsed.flag == 3:

            def cng(data: bytes) -> bytes:
                with _impersonate_system():
                    return _cng_decrypt(data)

        return unwrap_master_key(parsed, cng_decrypt=cng)
    except Exception as exc:
        logger.warning(f"Could not derive the ABE master key: {exc}. Skipping v20 cookies.")
        return None
