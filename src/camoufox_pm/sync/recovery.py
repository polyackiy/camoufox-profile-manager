"""Bounded offline authority recovery with a fresh-process verification ceremony.

No keychain, source device secrets, network, or temporary files are consulted.
A recovered owner still has unknown membership/profile freshness. This module
proves possession of recovery material, not permission to launch old profiles.
"""

from __future__ import annotations

import base64
import os
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Annotated, BinaryIO

from pydantic import Field

from camoufox_pm.sync.identity import (
    AuthoritySecrets,
    OwnerRoot,
    canonical_bytes,
    digest,
    encode_key,
    parse_record,
)
from camoufox_pm.sync.protocol import WireModel
from camoufox_pm.sync.vault import MAX_PACKAGE, open_recovery, seal_recovery

RECOVERY_WORKER_FLAG = "--sync-recovery-verify"
MAX_REQUEST = 128 * 1024
VERIFY_TIMEOUT_SECONDS = 15
_ERROR = "Offline recovery verification failed"


class RecoveryError(ValueError):
    """Sanitized failure, safe to return without private material."""


class _RecoveryPayload(WireModel):
    version: Annotated[int, Field(ge=1, le=1)] = 1
    root: OwnerRoot
    authority: Annotated[str, Field(min_length=96, max_length=96, repr=False)]


class _VerificationRequest(WireModel):
    package: Annotated[str, Field(min_length=1, max_length=120000, repr=False)]
    secret: Annotated[str, Field(min_length=43, max_length=43, repr=False)]


def _decode(raw: str, limit: int) -> bytes:
    result = base64.b64decode(raw + "=" * (-len(raw) % 4), altchars=b"-_", validate=True)
    if len(result) > limit or encode_key(result) != raw:
        raise RecoveryError(_ERROR)
    return result


def _validate_authority(root: OwnerRoot, authority: AuthoritySecrets) -> None:
    canonical_bytes(root)
    if (
        encode_key(authority.authority_key.public_key().public_bytes_raw()) != root.authority_public
        or encode_key(authority.recovery_key.public_key().public_bytes_raw())
        != root.recovery_public
    ):
        raise RecoveryError(_ERROR)


def create_recovery(root: OwnerRoot, authority: AuthoritySecrets) -> tuple[bytes, str]:
    """Create encrypted owner-only recovery; secret must be retained separately."""
    try:
        _validate_authority(root, authority)
        payload = _RecoveryPayload(root=root, authority=encode_key(authority.to_bytes()))
        return seal_recovery(canonical_bytes(payload))
    except Exception:
        raise RecoveryError(_ERROR) from None


def open_owner_recovery(package: bytes, secret: str) -> tuple[OwnerRoot, AuthoritySecrets]:
    """Recover owner authority only; enrollment must generate a NEW device identity."""
    try:
        payload = parse_record(open_recovery(package, secret), _RecoveryPayload)
        authority = AuthoritySecrets.from_bytes(_decode(payload.authority, 72))
        _validate_authority(payload.root, authority)
        return payload.root, authority
    except Exception:
        raise RecoveryError(_ERROR) from None


def _worker_command() -> list[str]:
    if getattr(sys, "frozen", False):
        # Packaged entrypoints must dispatch this flag before ordinary application
        # initialization. A package without the hook fails verification closed.
        return [sys.executable, RECOVERY_WORKER_FLAG]
    # -I ignores user site, PYTHONPATH and cwd. Source checkouts need one explicitly
    # trusted code path; it is derived from this installed module, never the request.
    package_root = str(Path(__file__).resolve().parents[2])
    code = (
        f"import sys; sys.path.insert(0, {package_root!r}); "
        "from camoufox_pm.sync.recovery import recovery_worker_main; "
        "raise SystemExit(recovery_worker_main())"
    )
    return [sys.executable, "-I", "-c", code]


def _worker_environment() -> dict[str, str]:
    # Windows needs its OS directory to locate system DLLs. No HOME, USERPROFILE,
    # keyring/session address, proxy, credential or Python injection variables.
    return {key: os.environ[key] for key in ("SystemRoot", "WINDIR") if key in os.environ}


def verify_recovery_isolated(package: bytes, secret: str, expected_root: OwnerRoot) -> None:
    """Fresh process must reconstruct exactly the expected owner public identity.

    Input travels only through an anonymous stdin pipe. subprocess.run kills and
    drains the child on timeout. The worker emits only one public root digest;
    stderr is discarded so dependency errors cannot escape as secret-bearing logs.
    """
    try:
        expected = (digest(expected_root) + "\n").encode("ascii")
        if type(package) is not bytes or not 1 <= len(package) <= MAX_PACKAGE:
            raise RecoveryError(_ERROR)
        request = _VerificationRequest(package=encode_key(package), secret=secret)
        raw = canonical_bytes(request)
        if len(raw) > MAX_REQUEST:
            raise RecoveryError(_ERROR)
        result = subprocess.run(
            _worker_command(),
            input=raw,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=VERIFY_TIMEOUT_SECONDS,
            check=False,
            env=_worker_environment(),
            cwd=Path(sys.executable).resolve().parent,
        )
        if result.returncode != 0 or result.stdout != expected:
            raise RecoveryError(_ERROR)
    except Exception:
        raise RecoveryError(_ERROR) from None


def _windows_pipe(input_pipe: bool) -> BinaryIO:
    """Duplicate an inherited redirected pipe for a windowed frozen executable.

    Never open console devices or filesystem paths. CPython deliberately sets its
    standard streams to None in windowed builds, although the parent supplied
    anonymous pipes through STARTF_USESTDHANDLES.
    """
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.__dict__["WinDLL"]("kernel32", use_last_error=True)
    kernel.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.GetFileType.argtypes = [wintypes.HANDLE]
    kernel.GetFileType.restype = wintypes.DWORD
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.DuplicateHandle.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    kernel.DuplicateHandle.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.GetStdHandle((-10 if input_pipe else -11) & 0xFFFFFFFF)
    if handle in (None, ctypes.c_void_p(-1).value) or kernel.GetFileType(handle) != 3:
        raise RecoveryError(_ERROR)
    process = kernel.GetCurrentProcess()
    duplicate = wintypes.HANDLE()
    if not kernel.DuplicateHandle(process, handle, process, ctypes.byref(duplicate), 0, False, 2):
        raise RecoveryError(_ERROR)
    try:
        fd = msvcrt.__dict__["open_osfhandle"](
            duplicate.value,
            getattr(os, "O_BINARY", 0) | (os.O_RDONLY if input_pipe else os.O_WRONLY),
        )
    except BaseException:
        kernel.CloseHandle(duplicate)
        raise
    # fd now owns only our duplicated handle, not the interpreter's original.
    try:
        return os.fdopen(fd, "rb" if input_pipe else "wb")
    except BaseException:
        os.close(fd)
        raise


def recovery_worker_main() -> int:
    """Internal CLI hook. Never print exceptions, private material, or diagnostics."""
    try:
        with ExitStack() as stack:
            if sys.stdin is None:
                if sys.platform != "win32":
                    return 1
                source = stack.enter_context(_windows_pipe(True))
            else:
                source = sys.stdin.buffer
            if sys.stdout is None:
                if sys.platform != "win32":
                    return 1
                destination = stack.enter_context(_windows_pipe(False))
            else:
                destination = sys.stdout.buffer
            raw = source.read(MAX_REQUEST + 1)
            if len(raw) > MAX_REQUEST:
                return 1
            request = parse_record(raw, _VerificationRequest)
            root, _authority = open_owner_recovery(
                _decode(request.package, MAX_PACKAGE), request.secret
            )
            destination.write((digest(root) + "\n").encode("ascii"))
            destination.flush()
            return 0
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(recovery_worker_main())
