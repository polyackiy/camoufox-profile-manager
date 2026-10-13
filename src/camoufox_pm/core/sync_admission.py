"""Short local edit/enrollment admission, independent of live-browser leases."""

import asyncio
import os
import re
import stat
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path

from .leases import ProfileLocked
from .update_permissions import (
    PermissionProtectionError,
    create_private_directory,
    create_private_file,
    reject_reparse_paths,
    verify_private_path,
)

_admitted: ContextVar[frozenset[tuple[object, str]]] = ContextVar(
    "profile_admission", default=frozenset()
)


@asynccontextmanager
async def profile_admission(manager, profile_id: str):
    if type(profile_id) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", profile_id):
        raise ValueError("Invalid profile identity")
    database = getattr(getattr(manager.storage, "db", None), "db_path", None)
    root = (
        Path(database).absolute().parent
        if isinstance(database, (str, Path))
        else Path(manager.data_dir).absolute()
    )
    key = (asyncio.current_task(), str(root / profile_id))
    if key in _admitted.get():
        yield
        return
    directory = root / "profile-admission"
    if not directory.exists():
        try:
            create_private_directory(directory)
        except FileExistsError:
            pass
    path = directory / (profile_id + ".lock")
    reject_reparse_paths(path)
    try:
        if path.exists():
            raise FileExistsError
        descriptor = create_private_file(path)
    except (FileExistsError, PermissionProtectionError):
        verify_private_path(path)
        descriptor = os.open(
            path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        )
    with os.fdopen(descriptor, "r+b", buffering=0) as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ProfileLocked(profile_id)
        if os.name == "nt":
            import msvcrt

            if not info.st_size:
                handle.write(b"0")
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
            except OSError:
                raise ProfileLocked(profile_id) from None

            def unlock():
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise ProfileLocked(profile_id) from None

            def unlock():
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

        token = _admitted.set(_admitted.get() | {key})
        try:
            yield
        finally:
            _admitted.reset(token)
            unlock()
