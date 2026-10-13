"""Offline checkpoint, replacement and installed-process validation transaction.

Deployment adapters supply a locally trusted verifier and fixed executable. These
Python arguments are internal capabilities, never values from an HTTP request.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

from .maintenance import MaintenanceBlocked
from .update_checkpoint import restore_checkpoint
from .update_helper import DirectoryReplacement
from .update_permissions import create_private_file
from .update_service import UpdateService


class UpdateTransaction:
    def __init__(
        self,
        service: UpdateService,
        installation: Path,
        entrypoint: str,
        *,
        verify_tree: Callable[[Path], None],
        offline_check: Callable[[], bool],
        validation_timeout: int = 120,
        verify_previous: Callable[[Path], None] | None = None,
    ):
        self.service = service
        self.installation = Path(installation).absolute()
        relative = Path(entrypoint)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError("A fixed relative application entrypoint is required")
        self.entrypoint = relative
        self.verify_tree = verify_tree
        self.offline_check = offline_check
        self.validation_timeout = validation_timeout
        self.verify_previous = verify_previous
        if self.service.root.is_relative_to(self.installation):
            raise ValueError("Offline helper state must be outside the installation")

    def _offline(self):
        value = self.service.status()
        if self.service.gate.owner != value.get("transaction_id") or not self.offline_check():
            raise MaintenanceBlocked("Close every manager and browser before replacement")

    def _replacement(self):
        return DirectoryReplacement(self.service.root, self.installation, offline=self._offline)

    def prepare(
        self,
        package,
        *,
        db_path: Path,
        paths: Mapping[str, Path],
        required_paths: tuple[str, ...] = (),
        current_version: str = "",
    ) -> dict:
        manifest = package.manifest
        package.verify_tree()
        transaction = self.service.prepare(
            db_path=db_path,
            paths=paths,
            verified_package=package.path,
            verify_package=package.verify_tree,
            offline_check=self.offline_check,
            browser_version=manifest["browser_version"],
            target_version=manifest["version"],
            required_paths=required_paths,
            metadata={
                "manifest": manifest,
                "previous_version": current_version,
                "installation": str(self.installation),
                "entrypoint": str(self.entrypoint),
            },
        )
        if hasattr(self, "browser_target"):
            transaction["browser_sha256"] = self.browser_target.hashes["sha256"]
        transaction.update(
            manifest=manifest,
            previous_version=current_version,
            installation=str(self.installation),
            entrypoint=str(self.entrypoint),
        )
        checkpoint = json.loads((Path(transaction["checkpoint"]) / "journal.json").read_text())
        transaction["checkpoint_timestamp"] = checkpoint["created_at"]
        self.service._save(transaction)
        return transaction

    def begin_browser_install(self) -> None:
        with self.service._control():
            self._offline()
            value = self.service.status()
            if value["state"] not in {"prepared", "installing_browser", "browser_ready"}:
                raise MaintenanceBlocked(
                    "Browser installation is not allowed in this transaction state"
                )
            value.update(state="installing_browser", browser_install_started=True)
            self.service._save(value)

    def browser_installed(self, paired_path: Path | None = None) -> None:
        with self.service._control():
            self._offline()
            value = self.service.status()
            if value["state"] != "installing_browser":
                raise MaintenanceBlocked("Browser installation was not started")
            if paired_path is not None:
                paired_path = Path(paired_path).resolve()
                browser_root = Path(value["paths"]["browser"]).resolve()
                if not paired_path.is_relative_to(browser_root / "browsers" / "cpm-verified"):
                    raise ValueError("Paired browser must be in the authenticated local cache")
                marker = json.loads((paired_path / ".cpm-pair.json").read_text())
                if marker != {
                    "version": value["browser_version"],
                    "sha256": value.get("browser_sha256"),
                }:
                    raise ValueError(
                        "Paired browser cache identity differs from the authenticated target"
                    )
                value["paired_browser_path"] = str(paired_path)
            value["state"] = "browser_ready"
            self.service._save(value)

    def _validate(self, installation: Path):
        self.verify_tree(installation)
        executable = installation / self.entrypoint
        if executable.is_symlink() or not executable.is_file():
            raise ValueError("Installed application entrypoint is missing or unsafe")
        record = self.service.status()
        if hasattr(self, "browser_target"):
            from .update_browser_pair import install_paired_browser

            paired = install_paired_browser(
                self.browser_target,
                Path(record["paths"]["browser"]),
                offline_check=lambda: self._offline() is None,
            )
            if str(paired.resolve()) != record.get("paired_browser_path"):
                raise ValueError("Authenticated paired browser location changed")
        receipt = self.service.root / "validation.json"
        receipt.unlink(missing_ok=True)
        with os.fdopen(create_private_file(receipt), "w", encoding="utf-8") as stream:
            json.dump(
                {"transaction_id": record["transaction_id"], "manifest": record["manifest"]}, stream
            )
            stream.flush()
            os.fsync(stream.fileno())
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"PYTHONPATH", "PYTHONHOME", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES"}
        }
        env["CPM_UPDATE_STATE_DIR"] = str(self.service.root)
        # Only installed authenticated executable is allowed to run validation.
        subprocess.run(
            [str(executable), "--update-validate", str(receipt)],
            cwd=installation,
            env=env,
            check=True,
            timeout=self.validation_timeout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        response = json.loads(receipt.read_text())
        if response.get("validated") != record["transaction_id"]:
            raise ValueError("Installed process did not acknowledge validation")

    def _restore(self):
        from .sync_guard import invalidate_all_sync

        self._offline()
        value = self.service.status()
        invalidate_all_sync(Path(value["db_path"]).parent)
        restore_checkpoint(
            Path(value["checkpoint"]),
            db_path=Path(value["db_path"]),
            paths={key: Path(path) for key, path in value["paths"].items()},
            maintenance_check=lambda: self._offline() is None,
        )

    def run(self) -> dict:
        with self.service._control():
            value = self.service.status()
            if value["state"] in {"committed", "rolled_back"} and not self.service.gate.blocked:
                return value
            self._offline()
            replacement = self._replacement()
            if value["state"] == "restoring_data" and (
                not replacement.journal.exists()
                or replacement._read().get("transaction_id") != value["transaction_id"]
            ):
                self._restore()
                value["state"] = "rolled_back"
                self.service._save(value)
                self.service.gate.release(value["transaction_id"])
                return value
            if value["state"] == "installing_browser":
                raise MaintenanceBlocked(
                    "Complete the paired browser installation before replacement"
                )
            if replacement.journal.exists():
                previous = replacement._read()
                if previous.get("transaction_id") != value["transaction_id"]:
                    if previous["state"] not in {"accepted", "rolled_back"}:
                        raise MaintenanceBlocked("Resolve the previous replacement before applying")
                    history = self.service.root / "history"
                    history.mkdir(mode=0o700, exist_ok=True)
                    replacement.journal.rename(history / f"replacement-{previous['id']}.json")
            if not replacement.journal.exists():
                replacement.prepare(
                    Path(value["package"]),
                    verify_package=self.verify_tree,
                    transaction_id=value["transaction_id"],
                )
            state = replacement._read()["state"]
            if state == "accepted":
                value["state"] = "committed"
            elif state == "rolled_back":
                value["state"] = "rolled_back"
            elif state in {"rolling_back", "restoring_old", "restoring_data"}:
                replacement.rollback(
                    restore_data=self._restore, verify_retained=self.verify_previous
                )
                value["state"] = "rolled_back"
            else:
                value["state"] = "replacing"
                self.service._save(value)
                try:
                    replacement.apply(verify_package=self.verify_tree)
                    replacement.accept(validate=self._validate)
                    value["state"] = "committed"
                except Exception as exc:
                    # Persist rollback intent before retryable filesystem recovery.
                    value.update(state="rolling_back", error=type(exc).__name__)
                    self.service._save(value)
                    replacement.rollback(
                        restore_data=self._restore, verify_retained=self.verify_previous
                    )
                    value["state"] = "rolled_back"
            self.service._save(value)
            self.service.gate.release(value["transaction_id"])
            return value

    def rollback(self, confirmed_timestamp: str) -> dict:
        with self.service._control():
            value = self.service.status()
            if value.get("checkpoint_timestamp") != confirmed_timestamp:
                raise ValueError("Confirm the exact checkpoint timestamp; later data will be lost")
            self.service.gate.acquire(value["transaction_id"])
            self._offline()
            replacement = self._replacement()
            if (
                not replacement.journal.exists()
                or replacement._read().get("transaction_id") != value["transaction_id"]
            ):
                value["state"] = "restoring_data"
                self.service._save(value)
                self._restore()
            else:
                replacement.rollback(
                    restore_data=self._restore,
                    allow_accepted=True,
                    verify_retained=self.verify_previous,
                )
            value["state"] = "rolled_back"
            self.service._save(value)
            self.service.gate.release(value["transaction_id"])
            return value
