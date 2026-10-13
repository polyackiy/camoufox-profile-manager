"""Native Firefox-compatible locks, without launching a local browser."""

import os
import subprocess
import sys
from types import SimpleNamespace

import psutil
import pytest

from camoufox_pm.sync import quiescence as q


@pytest.fixture
def profile(tmp_path, monkeypatch):
    folder = tmp_path.resolve() / "browser"
    folder.mkdir()
    monkeypatch.setattr(q.psutil, "process_iter", lambda: iter(()))
    return folder


_NATIVE = r"""
import os,sys
from pathlib import Path
p=Path(sys.argv[1]) / ('parent.lock' if os.name=='nt' else '.parentlock')
if os.name=='nt':
 import ctypes
 from ctypes import wintypes
 k=ctypes.WinDLL('kernel32',use_last_error=True)
 k.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
 k.CreateFileW.restype=wintypes.HANDLE
 k.CloseHandle.argtypes=[wintypes.HANDLE]
 h=k.CreateFileW(str(p),0xC0000000,0,None,4,0x80,None)
 if h==ctypes.c_void_p(-1).value:
  print('blocked',flush=True);sys.exit(0)
 release=lambda:k.CloseHandle(h)
else:
 import fcntl
 fd=os.open(p,os.O_RDWR|os.O_CREAT,0o600)
 try: fcntl.lockf(fd,fcntl.LOCK_EX|fcntl.LOCK_NB,0,0,os.SEEK_SET)
 except BlockingIOError:
  os.close(fd);print('blocked',flush=True);sys.exit(0)
 release=lambda:os.close(fd)
print('acquired',flush=True)
if len(sys.argv)>2: sys.stdin.readline()
release()
"""


def native_probe(profile):
    return subprocess.run(
        [sys.executable, "-c", _NATIVE, str(profile)],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    ).stdout.strip()


def test_native_child_cannot_take_held_lock_and_can_after_release(profile):
    assert native_probe(profile) == "acquired"
    name = profile / ("parent.lock" if os.name == "nt" else ".parentlock")
    before = name.stat().st_ino
    with q.browser_profile_lock(profile):
        assert native_probe(profile) == "blocked"
    assert name.stat().st_ino == before
    assert native_probe(profile) == "acquired"


def test_native_child_lock_blocks_capture(profile):
    child = subprocess.Popen(
        [sys.executable, "-c", _NATIVE, str(profile), "hold"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == "acquired"
        with pytest.raises(q.QuiescenceError):
            with q.browser_profile_lock(profile):
                pytest.fail("A second process entered a live profile")
    finally:
        child.communicate("release\n", timeout=10)
        if child.poll() is None:
            child.kill()
    with q.browser_profile_lock(profile):
        pass


def test_same_process_guard_prevents_fcntl_reentry_and_alias(profile):
    with q.browser_profile_lock(profile):
        for alias in (profile, profile / ".." / profile.name):
            with pytest.raises(q.QuiescenceError):
                with q.browser_profile_lock(alias):
                    pytest.fail("Nested lock was acquired")
        assert native_probe(profile) == "blocked"


def test_body_exception_releases_native_lock_without_deleting_inode(profile):
    with pytest.raises(RuntimeError, match="caller failed"):
        with q.browser_profile_lock(profile):
            raise RuntimeError("caller failed")
    assert native_probe(profile) == "acquired"


@pytest.mark.skipif(os.name == "nt", reason="POSIX fixture")
@pytest.mark.parametrize("kind", ["symlink", "fifo", "hardlink", "directory"])
def test_unsafe_lock_entries_are_not_followed_or_removed(profile, kind):
    lock = profile / ".parentlock"
    if kind == "symlink":
        lock.symlink_to("/missing-target")
    elif kind == "fifo":
        os.mkfifo(lock)
    elif kind == "hardlink":
        other = profile / "valuable"
        other.write_text("keep")
        os.link(other, lock)
    else:
        lock.mkdir()
    before = lock.lstat()
    with pytest.raises(q.QuiescenceError) as error:
        with q.browser_profile_lock(profile):
            pytest.fail("Unsafe lock accepted")
    assert str(profile) not in str(error.value)
    assert lock.lstat().st_ino == before.st_ino


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink fixture")
def test_legacy_symlink_lock_fails_closed_even_if_stale(profile):
    old = profile / "lock"
    old.symlink_to("127.0.0.1:99999999")
    with pytest.raises(q.QuiescenceError):
        with q.browser_profile_lock(profile):
            pass
    assert old.is_symlink()


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink fixture")
def test_symlink_profile_fails_closed(profile):
    alias = profile.parent / "alias"
    alias.symlink_to(profile, target_is_directory=True)
    with pytest.raises(q.QuiescenceError):
        with q.browser_profile_lock(alias):
            pass


def process(name, args, cwd=None, parent=None):
    return SimpleNamespace(
        name=lambda: name, cmdline=lambda: args, cwd=lambda: str(cwd), parent=lambda: parent
    )


@pytest.mark.parametrize(
    "args",
    [
        ["camoufox", "-profile", "PROFILE"],
        ["camoufox", "--profile=PROFILE"],
        ["camoufox", "/profile", "PROFILE"],
    ],
)
def test_live_matching_browser_rejected(profile, monkeypatch, args):
    args = [arg.replace("PROFILE", str(profile)) for arg in args]
    candidate = process("camoufox", args)
    monkeypatch.setattr(q.psutil, "process_iter", lambda: iter([candidate]))
    with pytest.raises(q.QuiescenceError):
        with q.browser_profile_lock(profile):
            pass
    assert native_probe(profile) == "acquired"


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["firefox"],
        ["firefox", "-profile"],
        ["firefox", "-P", "default"],
        ["firefox", "-profile", "a", "-profile", "b"],
    ],
)
def test_ambiguous_browser_profile_fails_closed(profile, monkeypatch, args):
    monkeypatch.setattr(q.psutil, "process_iter", lambda: iter([process("firefox", args)]))
    with pytest.raises(q.QuiescenceError):
        with q.browser_profile_lock(profile):
            pass


def test_unrelated_explicit_profile_and_content_child_allowed(profile, monkeypatch):
    parent = process("firefox", ["firefox", "-profile", str(profile.parent / "other")])
    child = process("firefox", ["firefox", "-contentproc"], parent=parent)
    monkeypatch.setattr(
        q.psutil, "process_iter", lambda: iter([parent, child, process("python", [])])
    )
    with q.browser_profile_lock(profile):
        pass


def test_relative_browser_profile_resolved_against_its_cwd(profile, monkeypatch):
    candidate = process("camoufox", ["camoufox", "-profile", profile.name], cwd=profile.parent)
    monkeypatch.setattr(q.psutil, "process_iter", lambda: iter([candidate]))
    with pytest.raises(q.QuiescenceError):
        with q.browser_profile_lock(profile):
            pass


@pytest.mark.parametrize("error", [psutil.AccessDenied(123), psutil.NoSuchProcess(123)])
def test_inaccessible_candidate_denied_but_exited_process_ignored(profile, monkeypatch, error):
    def inaccessible():
        raise error

    candidate = SimpleNamespace(name=lambda: "camoufox", cmdline=inaccessible)
    monkeypatch.setattr(q.psutil, "process_iter", lambda: iter([candidate]))
    if isinstance(error, psutil.NoSuchProcess):
        with q.browser_profile_lock(profile):
            pass
    else:
        with pytest.raises(q.QuiescenceError):
            with q.browser_profile_lock(profile):
                pass


@pytest.mark.skipif(os.name == "nt", reason="Windows denies inode deletion while held")
def test_replaced_lock_inode_fails_closed_after_operation(profile):
    lock = profile / ".parentlock"
    with pytest.raises(q.QuiescenceError):
        with q.browser_profile_lock(profile):
            lock.unlink()
            lock.write_bytes(b"replacement")
    # Never delete a replacement inode belonging to someone else.
    assert lock.read_bytes() == b"replacement"


@pytest.fixture
def linux_profile(profile, monkeypatch):
    if os.name == "nt":
        pytest.skip("POSIX native locking required")
    monkeypatch.setattr(q.sys, "platform", "linux")
    return profile


def test_linux_fcntl_marker_preserved_with_native_lock(linux_profile):
    marker = linux_profile / "lock"
    marker.symlink_to(f"127.0.0.1:+{os.getpid()}")
    before = marker.lstat()
    with q.browser_profile_lock(linux_profile):
        assert native_probe(linux_profile) == "blocked"
    assert marker.lstat().st_ino == before.st_ino
    assert os.readlink(marker) == f"127.0.0.1:+{os.getpid()}"


@pytest.mark.parametrize(
    "target",
    [
        "/etc/passwd",
        "../outside",
        "127.0.0.1:123",
        "127.0.0.1:+0",
        "127.0.0.1:+-1",
        "127.0.0.1:+2147483648",
        "999.0.0.1:+123",
        "127.0.0.1:+123/../../outside",
        "127.0.0.1:+123\n",
    ],
)
def test_linux_legacy_marker_rejects_unproven_targets(linux_profile, target):
    marker = linux_profile / "lock"
    marker.symlink_to(target)
    before = marker.lstat()
    with pytest.raises(q.QuiescenceError) as error:
        with q.browser_profile_lock(linux_profile):
            pytest.fail("Unproven marker accepted")
    assert target not in str(error.value)
    assert marker.lstat().st_ino == before.st_ino
    assert os.readlink(marker) == target


def test_linux_marker_foreign_owner_rejected(linux_profile, monkeypatch):
    marker = linux_profile / "lock"
    marker.symlink_to("127.0.0.1:+123")
    original = type(marker).lstat

    def foreign(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path == marker:
            return SimpleNamespace(st_mode=info.st_mode, st_uid=os.geteuid() + 1, st_nlink=1)
        return info

    monkeypatch.setattr(type(marker), "lstat", foreign)
    with pytest.raises(q.QuiescenceError):
        with q.browser_profile_lock(linux_profile):
            pytest.fail("Foreign marker accepted")


def test_linux_marker_cannot_override_live_browser(linux_profile, monkeypatch):
    (linux_profile / "lock").symlink_to(f"127.0.0.1:+{os.getpid()}")
    candidate = process("camoufox", ["camoufox", "-profile", str(linux_profile)])
    monkeypatch.setattr(q.psutil, "process_iter", lambda: iter([candidate]))
    with pytest.raises(q.QuiescenceError):
        with q.browser_profile_lock(linux_profile):
            pytest.fail("Live browser accepted")


def test_linux_marker_change_during_capture_rejected(linux_profile):
    marker = linux_profile / "lock"
    marker.symlink_to("127.0.0.1:+123")
    with pytest.raises(q.QuiescenceError):
        with q.browser_profile_lock(linux_profile):
            marker.unlink()
            marker.symlink_to("127.0.0.1:+456")
    assert os.readlink(marker) == "127.0.0.1:+456"


def test_linux_marker_does_not_override_native_contention(linux_profile):
    (linux_profile / "lock").symlink_to("127.0.0.1:+123")
    test_native_child_lock_blocks_capture(linux_profile)


def test_linux_marker_recheck_os_error_is_sanitized(linux_profile, monkeypatch):
    marker = linux_profile / "lock"
    marker.symlink_to("127.0.0.1:+123")

    def denied(_):
        raise PermissionError("private-profile-path")

    with pytest.raises(q.QuiescenceError, match="legacy-marker-recheck: PermissionError") as error:
        with q.browser_profile_lock(linux_profile):
            monkeypatch.setattr(q.os, "readlink", denied)
    assert "private-profile-path" not in str(error.value)
