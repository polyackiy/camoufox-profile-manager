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


def _verify_installation_sddl(sddl: str, user_sid: str) -> None:
    """Public binaries may be readable; unrelated principals must never write.

    This policy is deliberately separate from credential/cache confidentiality.
    Native read_sddl canonicalizes owner/trustee aliases to exact binary SIDs.
    Unknown ACE types, rights, flags and descriptor components fail closed.
    """
    aliases = {
        "SY": "S-1-5-18",
        "BA": "S-1-5-32-544",
        "WD": "S-1-1-0",
        "BU": "S-1-5-32-545",
        "AU": "S-1-5-11",
        "OW": "S-1-3-4",
        "CO": "S-1-3-0",
    }
    trusted = {user_sid, "S-1-5-18", "S-1-5-32-544"}
    match = re.fullmatch(r"O:([^:]+)D:([^()]*)(.*)", sddl)
    if not match or aliases.get(match[1], match[1]) not in trusted:
        raise PermissionProtectionError("Windows installation parent has an untrusted owner")
    flags, aces = match[2], match[3]
    if flags.replace("P", "").replace("AI", "").replace("AR", ""):
        raise PermissionProtectionError("Windows installation DACL has unsupported flags")
    parsed = re.findall(r"\(([^()]*)\)", aces)
    if not parsed or "".join(f"({ace})" for ace in parsed) != aces:
        raise PermissionProtectionError("Windows installation DACL has invalid ACEs")
    rights_map = {
        "FA": 0x1F01FF,
        "FR": 0x120089,
        "FW": 0x120116,
        "FX": 0x1200A0,
        "GA": 0x10000000,
        "GR": 0x80000000,
        "GW": 0x40000000,
        "GX": 0x20000000,
        "RC": 0x20000,
        "SD": 0x10000,
        "WD": 0x40000,
        "WO": 0x80000,
    }
    known = 0xF01F01FF
    readonly = 0xA01200A9
    for ace in parsed:
        parts = ace.split(";")
        if len(parts) != 6:
            raise PermissionProtectionError("Windows installation DACL has unsupported ACEs")
        kind, ace_flags, rights, object_guid, inherited_guid, trustee = parts
        trustee = aliases.get(trustee, trustee)
        if (
            kind != "A"
            or object_guid
            or inherited_guid
            or ace_flags.replace("OI", "")
            .replace("CI", "")
            .replace("ID", "")
            .replace("IO", "")
            .replace("NP", "")
            or not re.fullmatch(r"S-[0-9]+(?:-[0-9]+)+", trustee)
        ):
            raise PermissionProtectionError("Windows installation DACL has unsupported ACEs")
        if re.fullmatch(r"0x[0-9a-fA-F]{1,8}", rights):
            mask = int(rights, 16)
        else:
            tokens = re.findall(r"..", rights)
            if (
                not tokens
                or "".join(tokens) != rights
                or any(token not in rights_map for token in tokens)
            ):
                raise PermissionProtectionError("Windows installation DACL has unknown rights")
            mask = 0
            for token in tokens:
                mask |= rights_map[token]
        owner_rights = trustee == "S-1-3-4"
        creator_owner_inherited = trustee == "S-1-3-0" and "IO" in ace_flags
        # OWNER_RIGHTS means the descriptor's already trusted owner. A
        # CREATOR_OWNER inherit-only ACE never grants access to this parent;
        # unrelated principals cannot create children under the other ACEs.
        if mask & ~known or (
            trustee not in trusted
            and not owner_rights
            and not creator_owner_inherited
            and mask & ~readonly
        ):
            raise PermissionProtectionError(
                "Windows installation parent grants unrelated write access"
            )


def verify_windows_installation_parent(path: Path) -> None:
    """Inspect binary installation ACLs without changing their permissions."""
    reject_reparse_paths(path)
    api = _WindowsSecurity()
    _verify_installation_sddl(api.read_sddl(path), api.user_sid)


def _canonical_sddl(value: str, owner_sid: str, trustees: list[str]) -> str:
    """Replace display aliases with identities read from the binary descriptor."""
    value = re.sub(r"^O:[^:]+(?=D:)", f"O:{owner_sid}", value)
    aces = re.findall(r"\(([^()]*)\)", value)
    if len(aces) != len(trustees):
        raise PermissionProtectionError("Private Windows DACL identity count differs")
    identities = iter(trustees)

    def canonical(match):
        parts = match[1].split(";")
        if len(parts) != 6:
            raise PermissionProtectionError("Private Windows DACL has unsupported ACEs")
        parts[5] = next(identities)
        return "(" + ";".join(parts) + ")"

    return re.sub(r"\(([^()]*)\)", canonical, value)


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
        self._bind(
            self.advapi,
            "GetSecurityDescriptorOwner",
            [pointer, ctypes.POINTER(pointer), ctypes.POINTER(wintypes.BOOL)],
            wintypes.BOOL,
        )
        self._bind(
            self.advapi,
            "GetSecurityDescriptorDacl",
            [
                pointer,
                ctypes.POINTER(wintypes.BOOL),
                ctypes.POINTER(pointer),
                ctypes.POINTER(wintypes.BOOL),
            ],
            wintypes.BOOL,
        )
        self._bind(
            self.advapi,
            "GetAclInformation",
            [pointer, pointer, wintypes.DWORD, ctypes.c_int],
            wintypes.BOOL,
        )
        self._bind(
            self.advapi, "GetAce", [pointer, wintypes.DWORD, ctypes.POINTER(pointer)], wintypes.BOOL
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

    def _sid_text(self, sid) -> str:
        text = ctypes.c_void_p()
        self._check(self.advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)))
        try:
            return ctypes.wstring_at(text)
        finally:
            self.kernel.LocalFree(text)

    def _descriptor_identities(self, descriptor) -> tuple[str, list[str]]:
        # SDDL aliases such as LA encode a local administrator SID. Resolve the
        # actual owner and every trustee from the binary descriptor, rather than
        # assuming display aliases equal the process token SID.
        owner = ctypes.c_void_p()
        defaulted = self.w.BOOL()
        self._check(
            self.advapi.GetSecurityDescriptorOwner(
                descriptor, ctypes.byref(owner), ctypes.byref(defaulted)
            )
        )
        if not owner.value:
            raise PermissionProtectionError("Private Windows descriptor has no owner")
        owner_sid = self._sid_text(owner)
        present = self.w.BOOL()
        dacl = ctypes.c_void_p()
        self._check(
            self.advapi.GetSecurityDescriptorDacl(
                descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)
            )
        )
        if not present.value or not dacl.value:
            raise PermissionProtectionError("Private Windows descriptor has a NULL DACL")

        class AclSizeInformation(ctypes.Structure):
            _fields_ = [("count", self.w.DWORD), ("used", self.w.DWORD), ("free", self.w.DWORD)]

        info = AclSizeInformation()
        self._check(self.advapi.GetAclInformation(dacl, ctypes.byref(info), ctypes.sizeof(info), 2))
        trustees = []
        for index in range(info.count):
            ace = ctypes.c_void_p()
            self._check(self.advapi.GetAce(dacl, index, ctypes.byref(ace)))
            if not ace.value:
                raise PermissionProtectionError("Private Windows DACL has no ACE")
            # ACCESS_ALLOWED_ACE = four-byte ACE_HEADER + DWORD Mask + SID.
            header = ctypes.string_at(ace, 4)
            if header[0] != 0 or int.from_bytes(header[2:4], "little") < 16:
                raise PermissionProtectionError("Private Windows DACL has unsupported ACEs")
            trustees.append(self._sid_text(ctypes.c_void_p(ace.value + 8)))
        return owner_sid, trustees

    def read_sddl(self, path: Path) -> str:
        size = self.w.DWORD()
        self.advapi.GetFileSecurityW(str(path), 0x5, None, 0, ctypes.byref(size))
        if not size.value:
            self._check(False)
        descriptor = ctypes.create_string_buffer(size.value)
        self._check(
            self.advapi.GetFileSecurityW(str(path), 0x5, descriptor, size, ctypes.byref(size))
        )
        owner_sid, trustees = self._descriptor_identities(descriptor)
        text = ctypes.c_void_p()
        self._check(
            self.advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(
                descriptor, 1, 0x5, ctypes.byref(text), None
            )
        )
        try:
            return _canonical_sddl(ctypes.wstring_at(text), owner_sid, trustees)
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
        os.chmod(path, 0o700 if path.is_dir() or path.stat().st_mode & stat.S_IXUSR else 0o600)
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
