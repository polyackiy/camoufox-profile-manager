"""Offline directory replacement engine for a future signed platform helper.

This module is not an executable installer. A platform adapter must authenticate
the package, stop every writer and supply checkpoint restoration and acceptance
callbacks. No HTTP endpoint may accept paths or verification callbacks from users.
The retained package and failed replacement are never deleted automatically.
"""

import hashlib
import json
import os
import stat
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from .update_permissions import (
    create_private_directory,
    create_private_file,
    verify_private_path,
    verify_windows_installation_parent,
)


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


def validate_installation_parent(parent: Path) -> Path:
    """Allow user-writable standard app directories without chmodding them."""
    parent = _plain_directory(parent)
    info = parent.stat()
    if os.name == "nt":
        verify_windows_installation_parent(parent)
        if not os.access(parent, os.W_OK):
            raise ReplacementError("Installation requires elevation; use the manual installer")
        # os.access on Windows ignores many ACL distinctions. Demonstrate actual
        # directory creation/removal with a private, empty same-parent probe.
        probe = parent / f".cpm-update-probe-{uuid4().hex}"
        try:
            create_private_directory(probe)
            probe.rmdir()
        except (OSError, RuntimeError) as exc:
            raise ReplacementError(
                "Installation requires elevation; use the manual installer"
            ) from exc
    elif (
        info.st_uid not in (0, os.getuid())
        or info.st_mode & 0o002
        or not os.access(parent, os.W_OK)
    ):
        raise ReplacementError("Installation parent must be trusted and user-writable")
    if os.name != "nt" and info.st_mode & 0o020:
        import grp

        try:
            group = grp.getgrgid(info.st_gid).gr_name
        except KeyError as exc:
            raise ReplacementError("Installation parent has an unknown writable group") from exc
        if group not in {"root", "wheel", "admin"}:
            raise ReplacementError("Installation parent has an untrusted writable group")
    return parent


def _tree_inventory(root: Path) -> dict[str, list[str | int]]:
    inventory: dict[str, list[str | int]] = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            info = path.lstat()
            relative = path.relative_to(root).as_posix()
            if getattr(info, "st_file_attributes", 0) & 0x400:
                raise ReplacementError("Retained application contains a reparse point")
            if stat.S_ISLNK(info.st_mode):
                target = os.readlink(path)
                if not path.resolve().is_relative_to(root.resolve()):
                    raise ReplacementError("Retained application link escapes installation")
                inventory[relative] = ["link", target]
            elif stat.S_ISDIR(info.st_mode):
                inventory[relative] = ["directory", stat.S_IMODE(info.st_mode)]
            elif stat.S_ISREG(info.st_mode):
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
                inventory[relative] = [
                    "file",
                    stat.S_IMODE(info.st_mode),
                    info.st_size,
                    digest.hexdigest(),
                ]
            else:
                raise ReplacementError("Retained application contains a special file")
    return inventory


def _verify_retained(record: dict, root: Path) -> None:
    if record.get("previous_inventory") != _tree_inventory(root):
        raise ReplacementError("Retained application changed; manual recovery is required")


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
        self.installation = validate_installation_parent(installation.parent) / installation.name
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

    def prepare(
        self,
        staged: Path,
        *,
        verify_package: Callable[[Path], None],
        transaction_id: str | None = None,
    ) -> None:
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
        record = {
            "id": uuid4().hex,
            "installation": str(installed),
            "staged": str(staged),
            "previous_inventory": _tree_inventory(installed),
        }
        if transaction_id is not None:
            record["transaction_id"] = transaction_id
        new, _, _ = self._paths(record)
        # Publishing the preparation record before the rename makes an interrupted
        # preparation distinguishable from an installation replacement.
        self._write(record, "prepared")
        staged.rename(new)
        _sync(new.parent)

    def apply(self, *, verify_package: Callable[[Path], None]) -> None:
        self.offline()
        record = self._read()
        state = record["state"]
        if state == "validating":
            verify_package(_plain_directory(self.installation))
            return
        if state not in {"prepared", "moving_old", "old_retained", "moving_new"}:
            raise ReplacementError("Replacement cannot be applied in this state")
        new, old, _ = self._paths(record)
        if state == "prepared" and not new.exists():
            staged = _plain_directory(Path(record["staged"]))
            verify_package(staged)
            self.offline()
            staged.rename(new)
            _sync(new.parent)
        if state in {"prepared", "moving_old"}:
            verify_package(_plain_directory(new))
            if old.exists():
                if self.installation.exists():
                    raise ReplacementError("Ambiguous interrupted replacement")
            else:
                _plain_directory(self.installation)
                _verify_retained(record, self.installation)
                self._write(record, "moving_old")
                self.offline()
                self.installation.rename(old)
                _sync(old.parent)
            self._write(record, "old_retained")
        if new.exists():
            if self.installation.exists():
                raise ReplacementError("Replacement destination is occupied")
            verify_package(_plain_directory(new))
            self._write(record, "moving_new")
            self.offline()
            new.rename(self.installation)
            _sync(new.parent)
        elif not self.installation.is_dir():
            raise ReplacementError("New application is missing")
        verify_package(_plain_directory(self.installation))
        self._write(record, "validating")

    def accept(self, *, validate: Callable[[Path], None]) -> None:
        self.offline()
        record = self._read()
        if record["state"] != "validating":
            raise ReplacementError("Only a replacement awaiting validation can be accepted")
        validate(_plain_directory(self.installation))
        self._write(record, "accepted")

    def rollback(
        self,
        *,
        restore_data: Callable[[], None],
        allow_accepted: bool = False,
        verify_retained: Callable[[Path], None] | None = None,
    ) -> None:
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
            _verify_retained(record, self.installation)
            if verify_retained is not None:
                verify_retained(self.installation)
            self._write(record, "restoring_data")
            restore_data()
            self._write(record, "rolled_back")
            return
        if state != "restoring_data":
            if state != "restoring_old":
                self._write(record, "rolling_back")
            if old.exists():
                _plain_directory(old)
                _verify_retained(record, old)
                if verify_retained is not None:
                    verify_retained(old)
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
            _verify_retained(record, self.installation)
            if verify_retained is not None:
                verify_retained(self.installation)
            self._write(record, "restoring_data")
        _verify_retained(record, self.installation)
        if verify_retained is not None:
            verify_retained(self.installation)
        restore_data()
        self._write(record, "rolled_back")
