"""Explicit offline historical recovery; never restores synchronization authority.

Signed historical material cannot prove latest membership or checkout state. Output
is a private validated archive for importing as a NEW local profile, with a new
identity/namespace if synchronization is subsequently enabled.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import stat
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import Field, field_validator

from camoufox_pm.core.update_permissions import (
    create_private_directory,
    reject_reparse_paths,
    verify_private_path,
)
from camoufox_pm.sync.crypto import decrypt_file
from camoufox_pm.sync.identity import OwnerRoot, SignedMembership, decode_key, verify_membership
from camoufox_pm.sync.protocol import (
    SnapshotContext,
    SnapshotManifest,
    WireModel,
    signing_bytes,
    validate_context,
)
from camoufox_pm.sync.recovery import open_owner_recovery
from camoufox_pm.sync.snapshots import Compatibility, SnapshotMetadata, validate_snapshot
from camoufox_pm.sync.vault import MAX_PACKAGE

MAX_EVIDENCE_BYTES = 64 * 1024 * 1024
MAX_MEMBERSHIPS = 10000
_ERROR = "Historical recovery failed; verify the package, evidence and compatibility"


class HistoricalRecoveryError(ValueError):
    """Fixed error without credential, profile or filesystem details."""


class RecoveryEvidence(WireModel):
    version: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    root: OwnerRoot
    memberships: Annotated[
        tuple[SignedMembership, ...], Field(min_length=1, max_length=MAX_MEMBERSHIPS)
    ]
    manifest: SnapshotManifest
    signature: Annotated[str, Field(min_length=86, max_length=86)]

    @field_validator("memberships", mode="before")
    @classmethod
    def wire_array(cls, value):
        return tuple(value) if isinstance(value, list) else value


@dataclass(frozen=True)
class HistoricalRecovery:
    source_owner_id: str
    source_profile_id: str
    source_revision_id: str
    directory: Path
    metadata: SnapshotMetadata = field(repr=False)
    historical_fork: bool = True


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise HistoricalRecoveryError(_ERROR)
        result[key] = value
    return result


def _parse(raw: bytes) -> RecoveryEvidence:
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_EVIDENCE_BYTES:
        raise HistoricalRecoveryError(_ERROR)
    depth, quoted, escaped = 0, False, False
    for char in raw:
        if quoted:
            if escaped:
                escaped = False
            elif char == 92:
                escaped = True
            elif char == 34:
                quoted = False
        elif char == 34:
            quoted = True
        elif char in (91, 123):
            depth += 1
            if depth > 16:
                raise HistoricalRecoveryError(_ERROR)
        elif char in (93, 125):
            depth -= 1

    def reject_constant(value):
        raise HistoricalRecoveryError(_ERROR)

    return RecoveryEvidence.model_validate(
        json.loads(raw, object_pairs_hook=_pairs, parse_constant=reject_constant)
    )


def _validate(evidence: RecoveryEvidence):
    previous = None
    for membership in evidence.memberships:
        verify_membership(evidence.root, membership, previous=previous)
        previous = membership
    manifest = evidence.manifest
    if (
        previous is None
        or manifest.owner_id != evidence.root.owner_id
        or manifest.membership_epoch != previous.membership.epoch
    ):
        raise HistoricalRecoveryError(_ERROR)
    writer = next(
        (
            device
            for device in previous.membership.devices
            if device.device_id == manifest.writer_device_id
        ),
        None,
    )
    if writer is None:
        raise HistoricalRecoveryError(_ERROR)
    signature = decode_key(evidence.signature, 64)
    writer_key = Ed25519PublicKey.from_public_bytes(decode_key(writer.signing_public))
    writer_key.verify(signature, signing_bytes(manifest))
    recipients: tuple[tuple[Literal["device", "recovery"], str], ...] = tuple(
        sorted(
            [("device", device.device_id) for device in previous.membership.devices]
            + [("recovery", evidence.root.recovery_id)]
        )
    )
    context = SnapshotContext(
        **{
            key: getattr(manifest, key)
            for key in (
                "owner_id",
                "profile_id",
                "revision_id",
                "parent_revision_id",
                "writer_device_id",
                "membership_epoch",
                "checkout_generation",
            )
        },
        recipient_identities=recipients,
    )
    validate_context(manifest, context)
    return context, writer_key, signature


def build_recovery_evidence(
    root: OwnerRoot,
    memberships: tuple[SignedMembership, ...],
    manifest: SnapshotManifest,
    signature: str,
) -> bytes:
    """Package an authenticated historical chain; no current-head claim is made."""
    try:
        evidence = RecoveryEvidence(
            root=root, memberships=memberships, manifest=manifest, signature=signature
        )
        _validate(evidence)
        raw = json.dumps(
            evidence.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
        if len(raw) > MAX_EVIDENCE_BYTES:
            raise HistoricalRecoveryError(_ERROR)
        return raw
    except Exception:
        raise HistoricalRecoveryError(_ERROR) from None


def recover_archive(
    package: bytes,
    secret: str,
    evidence: bytes,
    ciphertext: Path,
    destination: Path,
    compatibility: Compatibility,
    *,
    acknowledge_historical_fork: bool,
) -> HistoricalRecovery:
    """Create NEW private output only; caller must assign fresh local profile ID.

    All metadata/chain/root checks precede output creation. On validation failure
    only this call's newly created output is removed. Original inputs stay intact.
    No keys, sessions, grants, membership state or launch permission are installed.
    """
    created = False
    try:
        if acknowledge_historical_fork is not True:
            raise HistoricalRecoveryError(_ERROR)
        parsed = _parse(evidence)
        root, authority = open_owner_recovery(package, secret)
        if parsed.root != root:
            raise HistoricalRecoveryError(_ERROR)
        context, writer, signature = _validate(parsed)
        destination = Path(destination).absolute()
        reject_reparse_paths(destination)
        verify_private_path(destination.parent)
        create_private_directory(destination)
        created = True
        archive = destination / "snapshot.zip"
        decrypt_file(
            ciphertext,
            archive,
            parsed.manifest,
            signature,
            context,
            "recovery",
            root.recovery_id,
            authority.recovery_key,
            writer,
        )
        metadata = validate_snapshot(archive, destination / "validated", compatibility)
        return HistoricalRecovery(
            root.owner_id,
            parsed.manifest.profile_id,
            parsed.manifest.revision_id,
            destination,
            metadata,
        )
    except Exception:
        if created:
            shutil.rmtree(destination, ignore_errors=True)
        raise HistoricalRecoveryError(_ERROR) from None


def _read_regular(path: Path, limit: int) -> bytes:
    reject_reparse_paths(path)
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or not 1 <= info.st_size <= limit:
            raise HistoricalRecoveryError(_ERROR)
        result = source.read(limit + 1)
        if len(result) > limit:
            raise HistoricalRecoveryError(_ERROR)
        return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Recover a historical snapshot as a NEW offline copy"
    )
    for name in ("package", "evidence", "ciphertext", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--browser-build", required=True)
    parser.add_argument("--os", choices=("linux", "windows", "macos"), required=True)
    parser.add_argument("--architecture", choices=("x86_64", "aarch64"), required=True)
    parser.add_argument("--acknowledge-historical-fork", action="store_true", required=True)
    args = parser.parse_args(argv)
    try:
        compatibility = Compatibility(
            browser_build=args.browser_build, os=args.os, architecture=args.architecture
        )
        package = _read_regular(args.package, MAX_PACKAGE)
        evidence = _read_regular(args.evidence, MAX_EVIDENCE_BYTES)
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            secret = getpass.getpass("Offline recovery secret: ")
        recover_archive(
            package,
            secret,
            evidence,
            args.ciphertext,
            args.output,
            compatibility,
            acknowledge_historical_fork=args.acknowledge_historical_fork,
        )
        print(
            "Historical archive verified. Import as a NEW local profile; old synchronization is not resumed."
        )
        return 0
    except Exception:
        print(_ERROR)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
