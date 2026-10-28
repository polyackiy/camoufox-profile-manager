"""Diagnostic metadata has a finite vocabulary and never formats private errors."""

import errno
import json
import sqlite3
import subprocess
import sys
import zipfile

import pytest

from camoufox_pm.core.leases import ProfileLocked
from camoufox_pm.core.operation_diagnostics import operation_diagnostic, validated_diagnostic
from camoufox_pm.core.update_permissions import PermissionProtectionError


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (OSError(errno.EACCES, "private-path"), "permission_denied"),
        (OSError(errno.EPERM, "private-path"), "permission_denied"),
        (OSError(errno.ENOSPC, "private-path"), "disk_full"),
        (OSError(errno.ENOENT, "private-path"), "missing_file"),
        (OSError(errno.EBUSY, "private-path"), "resource_busy"),
        (OSError(errno.EIO, "private-path"), "filesystem_failed"),
        (TimeoutError("private-host"), "deadline_exceeded"),
        (sqlite3.OperationalError("private-query"), "sqlite_operational"),
        (sqlite3.ProgrammingError("private-query"), "sqlite_programming"),
        (sqlite3.IntegrityError("private-query"), "sqlite_integrity"),
        (sqlite3.DatabaseError("private-query"), "sqlite_failed"),
        (ImportError("private-package"), "dependency_missing"),
        (PermissionProtectionError("private-descriptor"), "private_permissions"),
        (ProfileLocked("private-profile", "private-holder"), "profile_busy"),
        (ValueError("private-value"), "invalid_state"),
        (RuntimeError("private-value"), "internal"),
    ],
)
def test_error_categories_never_export_private_values(error, category):
    result = operation_diagnostic(error, area="sync", stage="execute").model_dump(mode="json")
    assert result["category"] == category
    assert "private-" not in json.dumps(result)
    assert result["site"] is None and result["line"] is None


def test_unknown_exception_names_str_repr_and_attributes_are_not_exported():
    class NoisyError(Exception):
        def __str__(self):
            raise AssertionError("Exception text must not be formatted")

        def __repr__(self):
            raise AssertionError("Exception repr must not be formatted")

    NoisyError.__name__ = "private_password_and_profile"
    error = NoisyError("private-credential")
    error.request = "private-url"
    result = operation_diagnostic(error, area="backup", stage="backup_profile")
    assert result.category == "internal"
    assert "private" not in result.model_dump_json()


def test_allowlisted_site_and_line_do_not_export_source_or_data_paths(tmp_path):
    private = tmp_path / "private-profile-cookie-and-password.zip"
    try:
        zipfile.ZipFile(private)
    except OSError as error:
        result = operation_diagnostic(error, area="backup", stage="backup_profile")
    assert result.site == "stdlib.zipfile"
    assert 1 <= result.line <= 10000
    assert result.category == "missing_file"
    assert (
        "private" not in result.model_dump_json() and str(tmp_path) not in result.model_dump_json()
    )


def test_user_code_cannot_claim_an_allowlisted_module_name():
    namespace = {"__name__": "camoufox_pm.core.backups"}
    code = compile("raise ValueError('private-credential')", "/private/user-script.py", "exec")
    try:
        exec(code, namespace)
    except ValueError as error:
        result = operation_diagnostic(error, area="backup", stage="backup_profile")
    assert result.site is None and result.line is None
    assert "private" not in result.model_dump_json()


def diagnostic():
    return {"version": 1, "area": "sync", "stage": "execute", "category": "internal"}


@pytest.mark.parametrize(
    "change",
    [
        {"version": 2},
        {"version": True},
        {"version": "1"},
        {"category": "private-exception"},
        {"stage": "private-user-url"},
        {"site": "/private/path"},
        {"site": "sync.runtime", "line": "42"},
        {"site": "sync.runtime", "line": True},
        {"site": "sync.runtime", "line": 10001},
        {"site": "sync.runtime", "line": -1},
        {"line": 42},
        {"password": "private-credential"},
        {"frames": [{"path": "private-file"}]},
        {"area": "backup"},
    ],
)
def test_export_rejects_unknown_or_private_metadata(change):
    assert validated_diagnostic({**diagnostic(), **change}, area="sync") is None


def test_export_requires_explicit_version_and_plain_json_object():
    value = diagnostic()
    del value["version"]
    assert validated_diagnostic(value, area="sync") is None
    assert validated_diagnostic(None, area="sync") is None
    assert validated_diagnostic([diagnostic()], area="sync") is None
    assert validated_diagnostic(diagnostic(), area="sync") == diagnostic()


def test_formatter_does_not_import_optional_sync_or_initialize_keys():
    code = """
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.startswith('camoufox_pm.sync'):
        raise AssertionError('Optional sync import during formatting')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from camoufox_pm.core.operation_diagnostics import operation_diagnostic
assert operation_diagnostic(RuntimeError('private-secret'), area='sync', stage='execute').category == 'internal'
"""
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, timeout=30)


@pytest.mark.parametrize("spoof", [False, True])
def test_unknown_or_spoofed_wrapper_cannot_forward_a_site(spoof):
    from camoufox_pm.core.operation_diagnostics import OperationDiagnostic

    kind = type(
        "ControlError" if spoof else "UnknownError",
        (RuntimeError,),
        {"__module__": "camoufox_pm.sync.control" if spoof else "private-module"},
    )
    error = kind("private-value")
    error._cpm_operation_diagnostic = OperationDiagnostic(
        version=1, area="sync", stage="execute", category="disk_full", site="sync.control", line=10
    )
    result = operation_diagnostic(error, area="sync", stage="execute")
    assert result.site is None and result.line is None and result.category == "internal"


def test_constructed_invalid_forwarded_model_does_not_escape_or_warn():
    from camoufox_pm.core.operation_diagnostics import OperationDiagnostic
    from camoufox_pm.sync.control import ControlError

    error = ControlError("private-original")
    error._cpm_operation_diagnostic = OperationDiagnostic.model_construct(
        version=1,
        area="sync",
        stage="execute",
        category="private-secret",
        site="private-path",
        line=1,
    )
    result = operation_diagnostic(error, area="sync", stage="execute")
    assert result.site is None and result.category == "invalid_state"
    assert "private-" not in result.model_dump_json()
