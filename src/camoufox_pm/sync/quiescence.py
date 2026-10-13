"""Hold Firefox's native profile lock while inspecting/capturing closed data.

Matches Mozilla toolkit/profile/nsProfileLock.cpp: whole-file POSIX fcntl write
lock on .parentlock; Windows exclusive CreateFile on parent.lock. No symlink/NFS
fallback and no stale-lock deletion. Linux obsolete IPv4:+PID legacy symlinks
are inspected without following them only after the native fcntl lock is held.
The manager still needs its local lease and maintenance/checkout gate. Raw launches by a hostile local OS account are outside
this cooperative boundary. Directory replacement must retain/reacquire protection
of the activated directory: holding an old renamed inode cannot lock a new one.
"""

from __future__ import annotations

import ipaddress
import os
import re
import stat
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

import psutil

from camoufox_pm.core.update_permissions import reject_reparse_paths

_ERROR = "Browser profile is active or its closed state cannot be verified"
_registry_guard = threading.Lock()
_held: set[str] = set()
_BROWSER_NAMES = {
    "firefox",
    "firefox-bin",
    "camoufox",
    "camoufox-bin",
    "firefox.exe",
    "camoufox.exe",
}


class QuiescenceError(ValueError):
    """Safe failure without process commandlines, profile paths or credentials."""


def _after_fork() -> None:
    global _registry_guard, _held
    _registry_guard = threading.Lock()
    _held = set()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)


def _process_profile(process, depth: int = 0) -> Path:
    if depth > 8:
        raise QuiescenceError(_ERROR)
    command = process.cmdline()
    found: list[str] = []
    for index, arg in enumerate(command[1:], 1):
        lowered = arg.lower()
        if lowered in {"-profile", "--profile", "/profile"}:
            if index + 1 >= len(command):
                raise QuiescenceError(_ERROR)
            found.append(command[index + 1])
        elif lowered.startswith(("-profile=", "--profile=", "/profile=")):
            found.append(arg.split("=", 1)[1])
    if not found and "-contentproc" in command:
        parent = process.parent()
        if parent is None or parent.name().lower() not in _BROWSER_NAMES:
            raise QuiescenceError(_ERROR)
        return _process_profile(parent, depth + 1)
    if len(found) != 1 or not found[0] or found[0].startswith("-"):
        raise QuiescenceError(_ERROR)
    candidate = Path(found[0])
    if not candidate.is_absolute():
        candidate = Path(process.cwd()) / candidate
    return candidate.resolve()


def assert_no_browser_process(profile: Path) -> None:
    """Reject a matching browser or a recognizable browser with ambiguous state.

    Unrelated browser profiles with explicit readable paths are allowed. Content
    processes inherit their parent's explicit profile. Missing/denied commandlines
    for candidates fail closed; process exits during enumeration are harmless.
    """
    try:
        target = os.path.normcase(str(profile.resolve()))
        for process in psutil.process_iter():
            try:
                if process.name().lower() not in _BROWSER_NAMES:
                    continue
                candidate = _process_profile(process)
                if os.path.normcase(str(candidate)) == target:
                    raise QuiescenceError(_ERROR)
            except psutil.NoSuchProcess:
                continue
    except QuiescenceError:
        raise
    except Exception:
        raise QuiescenceError(_ERROR) from None


def _regular(path: Path) -> os.stat_result | None:
    reject_reparse_paths(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise QuiescenceError(_ERROR)
    return info


@contextmanager
def _posix_lock(path: Path):
    import fcntl

    before = _regular(path)
    fd = os.open(
        path,
        os.O_RDWR
        | os.O_CREAT
        | os.O_NONBLOCK
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        current = os.fstat(fd)
        named = path.lstat()
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_nlink != 1
            or current.st_uid != os.geteuid()
            or (named.st_dev, named.st_ino) != (current.st_dev, current.st_ino)
            or (
                before is not None
                and (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino)
            )
        ):
            raise QuiescenceError(_ERROR)
        fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB, 0, 0, os.SEEK_SET)
        yield
        # Never reopen/close this inode while held: POSIX record locks are
        # process scoped and closing ANY descriptor to it would drop the lock.
        reject_reparse_paths(path)
        after = path.lstat()
        if (after.st_dev, after.st_ino, after.st_nlink) != (current.st_dev, current.st_ino, 1):
            raise QuiescenceError(_ERROR)
    finally:
        os.close(fd)


def _linux_legacy_marker(path: Path):
    """Inspect Mozilla's obsolete fcntl-backed marker without following/removing it.

    nsProfileLock.cpp IsSymlinkStaleLock treats a +PID signature as obsolete once
    fcntl succeeds. Plain PID legacy/NFS locks are NOT covered by this proof.
    This helper may be called only while holding this profile's .parentlock.
    """
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISLNK(info.st_mode):
        regular = _regular(path)
        return None if regular is None else (regular.st_dev, regular.st_ino, regular.st_ctime_ns)
    if info.st_uid != os.geteuid() or info.st_nlink != 1:
        raise QuiescenceError(_ERROR)
    target = os.readlink(path)
    match = re.fullmatch(r"([0-9.]{7,15}):\+([1-9][0-9]{0,9})", target)
    if match is None or not 0 < int(match[2]) < 2**31:
        raise QuiescenceError(_ERROR)
    try:
        ipaddress.IPv4Address(match[1])
    except ValueError:
        raise QuiescenceError(_ERROR) from None
    after = path.lstat()
    if (after.st_dev, after.st_ino, after.st_ctime_ns) != (
        info.st_dev,
        info.st_ino,
        info.st_ctime_ns,
    ):
        raise QuiescenceError(_ERROR)
    return info.st_dev, info.st_ino, info.st_ctime_ns, target


@contextmanager
def _windows_lock(path: Path):
    import ctypes
    from ctypes import wintypes

    _regular(path)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetFileType.argtypes = [wintypes.HANDLE]
    kernel.GetFileType.restype = wintypes.DWORD

    class Information(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD),
            ("created", wintypes.FILETIME),
            ("accessed", wintypes.FILETIME),
            ("written", wintypes.FILETIME),
            ("volume", wintypes.DWORD),
            ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD),
            ("links", wintypes.DWORD),
            ("index_high", wintypes.DWORD),
            ("index_low", wintypes.DWORD),
        ]

    kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(Information)]
    kernel.GetFileInformationByHandle.restype = wintypes.BOOL
    # GENERIC_READ|WRITE, no sharing, OPEN_ALWAYS (never truncate),
    # FILE_FLAG_OPEN_REPARSE_POINT prevents following a swapped symlink.
    handle = kernel.CreateFileW(str(path), 0xC0000000, 0, None, 4, 0x200080, None)
    if handle == ctypes.c_void_p(-1).value:
        raise QuiescenceError(_ERROR)
    try:
        info = Information()
        if (
            not kernel.GetFileInformationByHandle(handle, ctypes.byref(info))
            or kernel.GetFileType(handle) != 1
            or info.attributes & (0x400 | 0x10)
            or info.links != 1
        ):
            raise QuiescenceError(_ERROR)
        yield
    finally:
        kernel.CloseHandle(handle)


@contextmanager
def browser_profile_lock(profile: Path):
    """Fail-fast context manager; retain the exact lock inode on every exit.

    Works only on local filesystems supporting Firefox's native lock. Legacy
    non-fcntl symlinks fail closed; obsolete Linux +PID markers are never unlinked.
    The same-process guard is required because POSIX fcntl locks are process scoped.
    """
    try:
        reject_reparse_paths(profile)
        profile = profile.resolve()
    except Exception:
        raise QuiescenceError(_ERROR) from None
    key = os.path.normcase(str(profile))
    with _registry_guard:
        if key in _held:
            raise QuiescenceError(_ERROR)
        _held.add(key)
    body_exception = False
    stage = "profile-check"
    try:
        reject_reparse_paths(profile)
        if not profile.is_dir():
            raise QuiescenceError(_ERROR)
        # Linux Firefox also writes an obsolete symlink marker alongside its
        # fcntl lock. Inspect it only AFTER the actual OS lock has been acquired.
        legacy = profile / "lock" if sys.platform.startswith("linux") else None
        stage = "lock-entry-check"
        for name in ("lock", "parent.lock", ".parentlock"):
            if legacy is not None and name == "lock":
                continue
            _regular(profile / name)
        lock = profile / ("parent.lock" if os.name == "nt" else ".parentlock")
        stage = "native-lock"
        with _windows_lock(lock) if os.name == "nt" else _posix_lock(lock):
            stage = "legacy-marker"
            marker = _linux_legacy_marker(legacy) if legacy is not None else None
            stage = "process-check"
            assert_no_browser_process(profile)
            try:
                yield
            except BaseException:
                body_exception = True
                raise
            stage = "legacy-marker-recheck"
            if legacy is not None and _linux_legacy_marker(legacy) != marker:
                raise QuiescenceError(_ERROR)
    except QuiescenceError:
        raise
    except Exception as exc:
        if body_exception:
            raise
        # Fixed stages and exception class only; never emit paths or commands.
        raise QuiescenceError(f"{_ERROR} ({stage}: {type(exc).__name__})") from None
    finally:
        with _registry_guard:
            _held.discard(key)
