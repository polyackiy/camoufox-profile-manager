"""Descriptor policy tests everywhere, native ACL/inheritance tests on Windows."""

import os

import pytest

from camoufox_pm.core.update_permissions import (
    PermissionProtectionError,
    _verify_sddl,
    _WindowsSecurity,
    create_private_directory,
    create_private_file,
    protect_path,
    verify_private_path,
)

USER = "S-1-5-21-123-456-789-1001"


@pytest.mark.parametrize("flags", ["P", "PAI", "PARAI"])
def test_windows_descriptor_policy(flags):
    _verify_sddl(
        f"O:{USER}D:{flags}(A;OICI;FA;;;SY)(A;OICI;FA;;;{USER})", USER, require_protected=True
    )


@pytest.mark.parametrize(
    "sddl",
    [
        f"O:{USER}D:P",
        f"O:{USER}D:NO_ACCESS_CONTROL",
        f"O:{USER}D:P(A;;FA;;;WD)(A;;FA;;;{USER})",
        f"O:{USER}D:P(A;;FA;;;BA)(A;;FA;;;{USER})",
        f"O:{USER}D:P(A;;FA;;;SY)(A;;FR;;;{USER})",
        f"O:{USER}D:P(A;IO;FA;;;SY)(A;;FA;;;{USER})",
        f"O:{USER}D:P(D;;FA;;;SY)(A;;FA;;;{USER})",
        f"O:BAD:OWNERD:P(A;;FA;;;SY)(A;;FA;;;{USER})",
        f"O:SYD:P(A;;FA;;;SY)(A;;FA;;;{USER})",
        f"O:{USER}D:(A;;FA;;;SY)(A;;FA;;;{USER})",
    ],
)
def test_windows_descriptor_rejects_broad_null_inherit_only_or_missing_protection(sddl):
    with pytest.raises(PermissionProtectionError):
        _verify_sddl(sddl, USER, require_protected=True)


def test_inherited_child_policy():
    _verify_sddl(f"O:{USER}D:AI(A;ID;FA;;;SY)(A;ID;FA;;;{USER})", USER, require_protected=False)


def test_private_storage_creation(tmp_path):
    root = tmp_path / "private"
    create_private_directory(root)
    path = root / "credentials"
    with os.fdopen(create_private_file(path), "wb") as handle:
        handle.write(b"private")
    verify_private_path(root)
    verify_private_path(path)
    assert path.read_bytes() == b"private"
    if os.name != "nt":
        assert root.stat().st_mode & 0o777 == 0o700
        assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises((FileExistsError, PermissionProtectionError)):
        create_private_file(path)


@pytest.mark.skipif(os.name != "nt", reason="Native Windows token/DACL/inheritance")
def test_native_windows_acl_and_child_inheritance(tmp_path):
    root = tmp_path / "private-ключ"
    create_private_directory(root)
    api = _WindowsSecurity()
    assert api.user_sid.startswith("S-1-")
    _verify_sddl(api.read_sddl(root), api.user_sid, require_protected=True)
    child = root / "child"
    child.mkdir()
    file = child / "secret.key"
    file.write_bytes(b"key")
    # These were created through ordinary APIs, exercising real OI/CI inheritance.
    verify_private_path(child, require_protected=False)
    verify_private_path(file, require_protected=False)
    protect_path(file)
    verify_private_path(file)
    assert file.read_bytes() == b"key"


@pytest.mark.skipif(os.name != "nt", reason="Native Windows SetFileSecurityW")
def test_native_windows_broad_acl_rejected_then_repaired(tmp_path):
    import ctypes

    api = _WindowsSecurity()
    path = tmp_path / "secret"
    with os.fdopen(create_private_file(path), "wb") as handle:
        handle.write(b"key")
    descriptor = ctypes.c_void_p()
    api._check(
        api.advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            f"O:{api.user_sid}D:P(A;;FA;;;WD)(A;;FA;;;{api.user_sid})",
            1,
            ctypes.byref(descriptor),
            None,
        )
    )
    try:
        api._check(api.advapi.SetFileSecurityW(str(path), 0x80000005, descriptor))
    finally:
        api.kernel.LocalFree(descriptor)
    with pytest.raises(PermissionProtectionError, match="unexpected access"):
        verify_private_path(path)
    protect_path(path)
    verify_private_path(path)


@pytest.mark.skipif(os.name != "nt", reason="Native Windows junction protection")
def test_native_windows_junction_rejected(tmp_path):
    import subprocess

    actual = tmp_path / "actual"
    actual.mkdir()
    junction = tmp_path / "junction"
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(actual)], check=True, capture_output=True
    )
    try:
        with pytest.raises(PermissionProtectionError, match="reparse"):
            create_private_directory(junction / "private")
    finally:
        junction.rmdir()


def test_windows_directory_dacl_requires_inheritance():
    with pytest.raises(PermissionProtectionError, match="inherit"):
        _verify_sddl(
            f"O:{USER}D:P(A;;FA;;;SY)(A;;FA;;;{USER})",
            USER,
            require_protected=True,
            require_inheritance=True,
        )


def test_unavailable_windows_security_fails_before_credentials(tmp_path, monkeypatch):
    from camoufox_pm.core import update_permissions as module

    path = tmp_path / "credential"

    class Unavailable:
        def __init__(self):
            raise PermissionProtectionError("ACL filesystem unsupported")

    with monkeypatch.context() as patch:
        patch.setattr(module, "_WindowsSecurity", Unavailable)
        patch.setattr(module.os, "name", "nt")
        with pytest.raises(PermissionProtectionError, match="unsupported"):
            create_private_file(path)
    assert not path.exists()


def test_windows_display_aliases_use_actual_binary_owner_and_trustees():
    from camoufox_pm.core.update_permissions import _canonical_sddl

    displayed = "O:LAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;LA)"
    canonical = _canonical_sddl(displayed, USER, ["S-1-5-18", USER])
    _verify_sddl(canonical, USER, require_protected=True, require_inheritance=True)
    wrong_owner = _canonical_sddl(displayed, "S-1-5-21-999", ["S-1-5-18", USER])
    with pytest.raises(PermissionProtectionError, match="owner"):
        _verify_sddl(wrong_owner, USER, require_protected=True)


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable modes")
def test_private_executable_keeps_owner_execute(tmp_path):
    path = tmp_path / "executable"
    path.write_bytes(b"#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    protect_path(path)
    assert path.stat().st_mode & 0o777 == 0o700
    verify_private_path(path)


def test_standard_binary_parent_acl_allows_admin_system_owner_and_public_reads():
    from camoufox_pm.core.update_permissions import _verify_installation_sddl

    sddl = f"O:{USER}D:AI(A;OICI;FA;;;BA)(A;OICI;FA;;;SY)(A;OICI;FA;;;{USER})(A;OICI;FRFX;;;BU)"
    _verify_installation_sddl(sddl, USER)
    # The binary-directory policy never changes the stricter private policy.
    with pytest.raises(PermissionProtectionError):
        _verify_sddl(sddl, USER, require_protected=False)


@pytest.mark.parametrize(
    "rights", ["FW", "FA", "GW", "GA", "SD", "WD", "WO", "0x40", "0x10000", "0x40000"]
)
def test_binary_parent_acl_denies_unrelated_mutation(rights):
    from camoufox_pm.core.update_permissions import _verify_installation_sddl

    with pytest.raises(PermissionProtectionError, match="unrelated write"):
        _verify_installation_sddl(f"O:{USER}D:AI(A;OICI;FA;;;{USER})(A;OICI;{rights};;;BU)", USER)


@pytest.mark.parametrize(
    "ace",
    [
        "(D;;FW;;;BU)",
        "(OA;;FR;;;BU)",
        "(A;CR;FR;;;BU)",
        "(A;;ZZ;;;BU)",
        "(A;;0x02000000;;;BU)",
        "(A;;FR;;;UNKNOWN)",
    ],
)
def test_binary_parent_acl_rejects_unknown_ace_semantics(ace):
    from camoufox_pm.core.update_permissions import _verify_installation_sddl

    with pytest.raises(PermissionProtectionError):
        _verify_installation_sddl(f"O:{USER}D:AI(A;OICI;FA;;;{USER}){ace}", USER)


@pytest.mark.parametrize("owner", ["BA", "SY"])
def test_administrative_binary_parent_owner_allowed(owner):
    from camoufox_pm.core.update_permissions import _verify_installation_sddl

    _verify_installation_sddl(f"O:{owner}D:AI(A;OICI;FA;;;{USER})(A;OICI;0x1200a9;;;WD)", USER)


def test_observed_windows_default_parent_owner_rights_acl():
    from camoufox_pm.core.update_permissions import _verify_installation_sddl

    _verify_installation_sddl(
        f"O:{USER}D:P(A;OICI;FA;;;S-1-5-18)(A;OICI;FA;;;S-1-5-32-544)(A;OICI;FA;;;S-1-3-4)", USER
    )


def test_creator_owner_full_access_allowed_only_inherit_only():
    from camoufox_pm.core.update_permissions import _verify_installation_sddl

    _verify_installation_sddl(f"O:{USER}D:AI(A;OICI;FA;;;{USER})(A;OICIIO;GA;;;CO)", USER)
    with pytest.raises(PermissionProtectionError, match="unrelated write"):
        _verify_installation_sddl(f"O:{USER}D:AI(A;OICI;FA;;;{USER})(A;OICI;GA;;;CO)", USER)


@pytest.mark.skipif(os.name != "nt", reason="Native default Windows ACL boundary")
def test_native_default_binary_parent_acl_kept_unchanged(tmp_path):
    from camoufox_pm.core.update_helper import validate_installation_parent

    api = _WindowsSecurity()
    before = api.read_sddl(tmp_path)
    assert validate_installation_parent(tmp_path) == tmp_path.resolve()
    assert api.read_sddl(tmp_path) == before
    assert not list(tmp_path.iterdir())
