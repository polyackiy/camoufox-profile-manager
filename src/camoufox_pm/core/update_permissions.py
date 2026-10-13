"""Verified private update storage, using protected Windows DACLs or POSIX modes.

Windows descriptors grant full access only to the process token's user and SYSTEM.
Directory ACEs inherit to files/subdirectories. No account-name lookup, shell, or
localized CLI parsing is involved. Failure to set or read back permissions fails
closed; unsupported filesystems must not receive checkpoint credentials.
"""

from __future__ import annotations

import ctypes
import os
import re
import stat
from pathlib import Path


class PermissionProtectionError(RuntimeError):
    """Private update storage could not be established or verified."""


def reject_reparse_paths(path: Path) -> None:
    for component in (path.absolute(), *path.absolute().parents):
        if component.is_symlink() or (
            component.exists() and getattr(component.lstat(), "st_file_attributes", 0) & 0x400
        ):
            raise PermissionProtectionError("Private paths cannot contain symlinks/reparse points")


def _normalize_sid(sid: str) -> str:
    return {"SY": "S-1-5-18"}.get(sid, sid)


def _verify_sddl(
    sddl: str, user_sid: str, *, require_protected: bool, require_inheritance: bool = False
) -> None:
    # Only the owner and DACL are requested from Windows. Unexpected components,
    # NULL/empty DACLs, conditional/object/deny ACEs, broad trustees, and reduced
    # rights all fail verification. Child inheritance permits the ID ACE flag.
    match = re.fullmatch(r"O:([^:]+)D:([^()]*)(.*)", sddl)
    if not match or _normalize_sid(match[1]) != user_sid:
        raise PermissionProtectionError("Private Windows descriptor has an unexpected owner")
    flags, aces = match[2], match[3]
    if require_protected and "P" not in flags:
        raise PermissionProtectionError("Private Windows DACL must be protected")
    if flags.replace("P", "").replace("AI", "").replace("AR", ""):
        raise PermissionProtectionError("Private Windows DACL has unsupported flags")
    allowed = {user_sid, "S-1-5-18"}
    actual = set()
    parsed = re.findall(r"\(([^()]*)\)", aces)
    if not parsed or "".join(f"({ace})" for ace in parsed) != aces:
        raise PermissionProtectionError("Private Windows DACL has invalid ACEs")
    for ace in parsed:
        parts = ace.split(";")
        if len(parts) != 6:
            raise PermissionProtectionError("Private Windows DACL has unsupported ACEs")
        kind, ace_flags, rights, object_guid, inherited_guid, trustee = parts
        trustee = _normalize_sid(trustee)
        if (
            kind != "A"
            or rights not in {"FA", "0x1f01ff"}
            or object_guid
            or inherited_guid
            or trustee not in allowed
            or ace_flags.replace("OI", "").replace("CI", "").replace("ID", "")
        ):
            raise PermissionProtectionError("Private Windows DACL grants unexpected access")
        if require_inheritance and ("OI" not in ace_flags or "CI" not in ace_flags):
            raise PermissionProtectionError("Private Windows directory DACL must inherit")
        actual.add(trustee)
    if actual != allowed:
        raise PermissionProtectionError("Private Windows DACL lacks required trustees")


class _WindowsSecurity:
    """Lazy ctypes bindings; never loaded on POSIX."""

    def __init__(self) -> None:
        from ctypes import wintypes

        self.w = wintypes
        self.advapi = ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        pointer = ctypes.c_void_p
        self._bind(self.kernel, "GetCurrentProcess", [], wintypes.HANDLE)
        self._bind(self.kernel, "CloseHandle", [wintypes.HANDLE], wintypes.BOOL)
        self._bind(self.kernel, "LocalFree", [pointer], pointer)
        self._bind(
            self.advapi,
            "OpenProcessToken",
            [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)],
            wintypes.BOOL,
        )
        self._bind(
            self.advapi,
            "GetTokenInformation",
            [
                wintypes.HANDLE,
                ctypes.c_int,
                pointer,
                wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD),
            ],
            wintypes.BOOL,
        )
        self._bind(
            self.advapi, "ConvertSidToStringSidW", [pointer, ctypes.POINTER(pointer)], wintypes.BOOL
        )
        self._bind(
            self.advapi,
            "ConvertStringSecurityDescriptorToSecurityDescriptorW",
            [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(pointer), pointer],
            wintypes.BOOL,
        )
        self._bind(
            self.advapi,
            "ConvertSecurityDescriptorToStringSecurityDescriptorW",
            [pointer, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(pointer), pointer],
            wintypes.BOOL,
        )
        self._bind(
            self.advapi,
            "SetFileSecurityW",
            [wintypes.LPCWSTR, wintypes.DWORD, pointer],
            wintypes.BOOL,
        )
        self._bind(
            self.advapi,
            "GetFileSecurityW",
            [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                pointer,
                wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD),
            ],
            wintypes.BOOL,
        )
        self._bind(self.kernel, "CreateDirectoryW", [wintypes.LPCWSTR, pointer], wintypes.BOOL)
        self._bind(
            self.kernel,
            "CreateFileW",
            [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
                pointer,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.HANDLE,
            ],
            wintypes.HANDLE,
        )
        self.user_sid = self._user_sid()

    @staticmethod
    def _bind(library, name, args, result) -> None:
        function = getattr(library, name)
        function.argtypes = args
        function.restype = result

    @staticmethod
    def _check(result) -> None:
        if not result:
            error = ctypes.get_last_error()  # type: ignore[attr-defined]
            raise PermissionProtectionError(f"Windows security API failed ({error})")

    def _user_sid(self) -> str:
        token = self.w.HANDLE()
        self._check(
            self.advapi.OpenProcessToken(
                self.kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)
            )
        )
        try:
            size = self.w.DWORD()
            self.advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
            if not size.value:
                self._check(False)
            buffer = ctypes.create_string_buffer(size.value)
            self._check(self.advapi.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)))
            # TOKEN_USER starts with SID_AND_ATTRIBUTES; its first field is PSID.
            sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
            text = ctypes.c_void_p()
            self._check(self.advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)))
            try:
                return ctypes.wstring_at(text)
            finally:
                self.kernel.LocalFree(text)
        finally:
            self.kernel.CloseHandle(token)

    def descriptor(self, *, directory: bool):
        inheritance = "OICI" if directory else ""
        trustees = sorted({self.user_sid, "S-1-5-18"})
        sddl = f"O:{self.user_sid}D:P" + "".join(
            f"(A;{inheritance};FA;;;{sid})" for sid in trustees
        )
        descriptor = ctypes.c_void_p()
        self._check(
            self.advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl, 1, ctypes.byref(descriptor), None
            )
        )
        return descriptor

    def read_sddl(self, path: Path) -> str:
        size = self.w.DWORD()
        self.advapi.GetFileSecurityW(str(path), 0x5, None, 0, ctypes.byref(size))
        if not size.value:
            self._check(False)
        descriptor = ctypes.create_string_buffer(size.value)
        self._check(
            self.advapi.GetFileSecurityW(str(path), 0x5, descriptor, size, ctypes.byref(size))
        )
        text = ctypes.c_void_p()
        self._check(
            self.advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(
                descriptor, 1, 0x5, ctypes.byref(text), None
            )
        )
        try:
            return ctypes.wstring_at(text)
        finally:
            self.kernel.LocalFree(text)

    def protect(self, path: Path) -> None:
        descriptor = self.descriptor(directory=path.is_dir())
        try:
            self._check(self.advapi.SetFileSecurityW(str(path), 0x80000005, descriptor))
        finally:
            self.kernel.LocalFree(descriptor)
        _verify_sddl(
            self.read_sddl(path),
            self.user_sid,
            require_protected=True,
            require_inheritance=path.is_dir(),
        )

    def create(self, path: Path, *, directory: bool) -> int | None:
        class SecurityAttributes(ctypes.Structure):
            _fields_ = [
                ("length", self.w.DWORD),
                ("descriptor", ctypes.c_void_p),
                ("inherit", self.w.BOOL),
            ]

        descriptor = self.descriptor(directory=directory)
        attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
        handle = None
        try:
            if directory:
                self._check(self.kernel.CreateDirectoryW(str(path), ctypes.byref(attributes)))
            else:
                # GENERIC_WRITE, share read/write/delete (ACL inspection), CREATE_NEW.
                handle = self.kernel.CreateFileW(
                    str(path), 0x40000000, 7, ctypes.byref(attributes), 1, 0x80, None
                )
                if handle == ctypes.c_void_p(-1).value:
                    handle = None
                    self._check(False)
            _verify_sddl(
                self.read_sddl(path),
                self.user_sid,
                require_protected=True,
                require_inheritance=path.is_dir(),
            )
            if handle is not None:
                import msvcrt

                fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)  # type: ignore[attr-defined]
                handle = None  # fd owns HANDLE now.
                return fd
            return None
        finally:
            if handle is not None:
                self.kernel.CloseHandle(handle)
            self.kernel.LocalFree(descriptor)


def protect_path(path: Path) -> None:
    """Set and verify current-user-only credentials (SYSTEM also on Windows)."""
    reject_reparse_paths(path)
    if os.name == "nt":
        _WindowsSecurity().protect(path)
    else:
        os.chmod(path, 0o700 if path.is_dir() else 0o600)
        verify_private_path(path)


def verify_private_path(path: Path, *, require_protected: bool = True) -> None:
    reject_reparse_paths(path)
    if os.name == "nt":
        api = _WindowsSecurity()
        _verify_sddl(
            api.read_sddl(path),
            api.user_sid,
            require_protected=require_protected,
            require_inheritance=path.is_dir(),
        )
    elif path.stat().st_uid != os.geteuid() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise PermissionProtectionError("Private update path permits other users")


def create_private_directory(path: Path) -> None:
    """Create one new directory with restrictive permissions from creation."""
    reject_reparse_paths(path)
    if os.name == "nt":
        _WindowsSecurity().create(path, directory=True)
    else:
        path.mkdir(mode=0o700, exist_ok=False)
        verify_private_path(path)


def create_private_file(path: Path) -> int:
    """Create one new private empty file and return its writable descriptor."""
    reject_reparse_paths(path)
    if os.name == "nt":
        descriptor = _WindowsSecurity().create(path, directory=False)
        assert descriptor is not None
        return descriptor
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        verify_private_path(path)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise
