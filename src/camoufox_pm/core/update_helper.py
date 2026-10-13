"""Offline directory replacement engine for a future signed platform helper.

This module is not an executable installer. A platform adapter must authenticate
the package, stop every writer and supply checkpoint restoration and acceptance
callbacks. No HTTP endpoint may accept paths or verification callbacks from users.
The retained package and failed replacement are never deleted automatically.
"""

import json
import os
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from .update_permissions import create_private_file, verify_private_path


class ReplacementError(RuntimeError):
    pass


def _sync(directory: Path) -> None:
    if os.name != "nt":
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _plain_directory(path: Path) -> Path:
    path = path.absolute()
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ReplacementError("Update paths must not contain symbolic links")
        if component.exists():
            info = component.stat()
            if getattr(info, "st_file_attributes", 0) & 0x400:
                raise ReplacementError("Update paths must not contain reparse points")
    if not path.is_dir():
        raise ReplacementError("An update directory is missing")
    return path.resolve()


class DirectoryReplacement:
    """Journalled same-volume swap; the caller owns a cross-process offline lock.

    Every callback must raise on failure. ``offline`` is called before each
    filesystem mutation, including recovery; it must verify the lock is still
    owned and every application/browser writer is stopped. A truthy value alone
    is deliberately not used as a substitute for an actual lock implementation.
    """

    def __init__(
        self, journal_dir: Path, installation: Path, *, offline: Callable[[], None]
    ) -> None:
        self.root = _plain_directory(journal_dir)
        verify_private_path(self.root)
        # The installation may be temporarily absent after a power failure.
        self.installation = _plain_directory(installation.parent) / installation.name
        verify_private_path(self.installation.parent)
        if self.root == self.installation or self.root.is_relative_to(self.installation):
            raise ReplacementError("The journal must be outside the installation")
        self.offline = offline
        self.journal = self.root / "replacement.json"

    def _write(self, record: dict, state: str) -> None:
        self.offline()
        record["state"] = state
        temporary = self.root / f".replacement-{uuid4().hex}.tmp"
        descriptor = create_private_file(temporary)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(record, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.journal)
            _sync(self.root)
        finally:
            temporary.unlink(missing_ok=True)

    def _read(self) -> dict:
        if self.journal.is_symlink() or not self.journal.is_file():
            raise ReplacementError("A private replacement journal is required")
        verify_private_path(self.journal)
        try:
            record = json.loads(self.journal.read_text(encoding="utf-8"))
            token = record["id"]
            if (
                not isinstance(token, str)
                or len(token) != 32
                or any(char not in "0123456789abcdef" for char in token)
            ):
                raise ValueError("Invalid transaction")
            if record["installation"] != str(self.installation):
                raise ValueError("Installation mismatch")
            if record["state"] not in {
                "prepared",
                "moving_old",
                "old_retained",
                "moving_new",
                "validating",
                "accepted",
                "rolling_back",
                "restoring_old",
                "restoring_data",
                "rolled_back",
            }:
                raise ValueError("Invalid state")
            return record
        except (KeyError, TypeError, ValueError) as exc:
            raise ReplacementError("Invalid replacement journal; manual recovery required") from exc

    def _paths(self, record: dict) -> tuple[Path, Path, Path]:
        prefix = f".{self.installation.name}-{record['id']}"
        parent = self.installation.parent
        return tuple(parent / f"{prefix}-{suffix}" for suffix in ("new", "old", "failed"))  # type: ignore[return-value]

    def prepare(self, staged: Path, *, verify_package: Callable[[Path], None]) -> None:
        self.offline()
        if self.journal.exists():
            raise ReplacementError("A replacement journal already exists")
        installed = _plain_directory(self.installation)
        staged = _plain_directory(staged)
        if (
            staged == installed
            or staged.is_relative_to(installed)
            or installed.is_relative_to(staged)
        ):
            raise ReplacementError("Staging must not overlap the installed application")
        if self.root == staged or self.root.is_relative_to(staged):
            raise ReplacementError("Staging must not contain the journal")
        if staged.stat().st_dev != installed.parent.stat().st_dev:
            raise ReplacementError("Stage the verified application on the installation volume")
        verify_package(staged)
        self.offline()
        record = {"id": uuid4().hex, "installation": str(installed)}
        new, _, _ = self._paths(record)
        # Publishing the preparation record before the rename makes an interrupted
        # preparation distinguishable from an installation replacement.
        self._write(record, "prepared")
        staged.rename(new)
        _sync(new.parent)

    def apply(self, *, verify_package: Callable[[Path], None]) -> None:
        self.offline()
        record = self._read()
        if record["state"] != "prepared":
            raise ReplacementError("Only a prepared replacement can be applied")
        new, old, _ = self._paths(record)
        _plain_directory(self.installation)
        _plain_directory(new)
        if old.exists():
            raise ReplacementError("Retained application path is occupied")
        # Recheck immediately before use; the earlier download check is insufficient.
        verify_package(new)
        self._write(record, "moving_old")
        self.installation.rename(old)
        _sync(old.parent)
        self._write(record, "old_retained")
        self._write(record, "moving_new")
        new.rename(self.installation)
        _sync(new.parent)
        self._write(record, "validating")

    def accept(self, *, validate: Callable[[Path], None]) -> None:
        self.offline()
        record = self._read()
        if record["state"] != "validating":
            raise ReplacementError("Only a replacement awaiting validation can be accepted")
        validate(_plain_directory(self.installation))
        self._write(record, "accepted")

    def rollback(self, *, restore_data: Callable[[], None], allow_accepted: bool = False) -> None:
        """Recover interrupted swaps; data restore must itself be restartable.

        After acceptance the caller must obtain explicit informed confirmation
        before passing allow_accepted. Recovery never silently loses new work.
        """
        self.offline()
        record = self._read()
        state = record["state"]
        if state == "rolled_back":
            return
        if state == "accepted" and not allow_accepted:
            raise ReplacementError("Recovery may discard newer data; confirmation is required")
        _, old, failed = self._paths(record)
        if state in {"prepared", "moving_old"} and not old.exists():
            _plain_directory(self.installation)
            self._write(record, "rolled_back")
            return
        if state != "restoring_data":
            if state != "restoring_old":
                self._write(record, "rolling_back")
            if old.exists():
                _plain_directory(old)
                if self.installation.exists():
                    _plain_directory(self.installation)
                    if failed.exists():
                        raise ReplacementError("Failed-package destination is occupied")
                    self.installation.rename(failed)
                    _sync(failed.parent)
                self._write(record, "restoring_old")
                old.rename(self.installation)
                _sync(old.parent)
            elif not (state == "restoring_old" and self.installation.is_dir()):
                raise ReplacementError("Retained package is missing; manual recovery required")
            self._write(record, "restoring_data")
        restore_data()
        self._write(record, "rolled_back")
