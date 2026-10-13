"""Offline recovery validates historical authority without creating a live device."""

import json
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

from camoufox_pm.core.models import Profile
from camoufox_pm.core.update_permissions import create_private_directory
from camoufox_pm.sync.crypto import encrypt_file
from camoufox_pm.sync.identity import bootstrap, encode_key
from camoufox_pm.sync.protocol import SUITE, SnapshotManifest
from camoufox_pm.sync.recovery import create_recovery
from camoufox_pm.sync.recovery_archive import (
    HistoricalRecoveryError,
    build_recovery_evidence,
    recover_archive,
)
from camoufox_pm.sync.snapshots import Compatibility, build_snapshot


@pytest.fixture
def bundle(tmp_path):
    directory = tmp_path.resolve() / "private"
    create_private_directory(directory)
    data = directory / "data"
    create_private_directory(data)
    (data / "cookies.sqlite").write_bytes(b"recovered private cookies")
    compatibility = Compatibility(browser_build="152-test", os="linux", architecture="x86_64")
    archive = directory / "original.zip"
    build_snapshot(
        Profile(name="Historical profile", notes="private metadata"), data, archive, compatibility
    )
    root, keys, membership = bootstrap("https://relay.example")
    device_id = membership.membership.devices[0].device_id
    recipients = {
        ("device", device_id): keys.device.encryption_key.public_key(),
        ("recovery", root.recovery_id): keys.recovery_key.public_key(),
    }
    template = SnapshotManifest(
        version=1,
        suite=SUITE,
        owner_id=root.owner_id,
        profile_id=str(uuid4()),
        revision_id=str(uuid4()),
        parent_revision_id=None,
        writer_device_id=device_id,
        membership_epoch=1,
        checkout_generation=1,
        ciphertext_sha256="0" * 64,
        ciphertext_length=41,
        recipients=tuple(
            {
                "kind": kind,
                "recipient_id": identity,
                "enc": encode_key(bytes(32)),
                "wrapped_key": encode_key(bytes(48)),
            }
            for kind, identity in sorted(recipients)
        ),
    )
    ciphertext = directory / "snapshot.enc"
    manifest, signature = encrypt_file(
        archive, ciphertext, template, recipients, keys.device.signing_key
    )
    evidence = build_recovery_evidence(root, (membership,), manifest, encode_key(signature))
    package, secret = create_recovery(root, keys.authority)
    return SimpleNamespace(
        directory=directory,
        ciphertext=ciphertext,
        evidence=evidence,
        package=package,
        secret=secret,
        compatibility=compatibility,
        manifest=manifest,
    )


def recover(bundle, **kwargs):
    values = {
        "package": bundle.package,
        "secret": bundle.secret,
        "evidence": bundle.evidence,
        "ciphertext": bundle.ciphertext,
        "destination": bundle.directory / "recovered",
        "compatibility": bundle.compatibility,
        "acknowledge_historical_fork": True,
    }
    values.update(kwargs)
    return recover_archive(**values)


def test_real_historical_recovery_creates_no_authority_or_device(bundle):
    result = recover(bundle)
    assert result.historical_fork
    assert result.source_revision_id == bundle.manifest.revision_id
    assert (
        result.directory / "validated/data/cookies.sqlite"
    ).read_bytes() == b"recovered private cookies"
    assert sorted(path.name for path in result.directory.iterdir()) == ["snapshot.zip", "validated"]
    assert not (result.directory / "installation").exists()
    if os.name != "nt":
        assert result.directory.stat().st_mode & 0o777 == 0o700
        assert (result.directory / "snapshot.zip").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "case", ["secret", "signature", "root", "chain", "writer", "ciphertext", "compatibility", "ack"]
)
def test_recovery_rejects_tampering_without_plaintext_output(bundle, case):
    kwargs = {}
    evidence = json.loads(bundle.evidence)
    if case == "secret":
        kwargs["secret"] = encode_key(bytes(32))
    elif case == "signature":
        evidence["signature"] = encode_key(bytes(64))
    elif case == "root":
        evidence["root"]["owner_id"] = str(uuid4())
    elif case == "chain":
        evidence["memberships"] = []
    elif case == "writer":
        evidence["manifest"]["writer_device_id"] = str(uuid4())
    elif case == "ciphertext":
        raw = bytearray(bundle.ciphertext.read_bytes())
        raw[-1] ^= 1
        bundle.ciphertext.write_bytes(raw)
    elif case == "compatibility":
        kwargs["compatibility"] = bundle.compatibility.model_copy(update={"browser_build": "old"})
    elif case == "ack":
        kwargs["acknowledge_historical_fork"] = False
    kwargs["evidence"] = json.dumps(evidence).encode()
    with pytest.raises(HistoricalRecoveryError) as error:
        recover(bundle, **kwargs)
    assert "private" not in str(error.value)
    assert not (bundle.directory / "recovered").exists()


def test_existing_destination_never_removed_or_overwritten(bundle):
    destination = bundle.directory / "recovered"
    create_private_directory(destination)
    (destination / "keep").write_text("original")
    with pytest.raises(HistoricalRecoveryError):
        recover(bundle)
    assert (destination / "keep").read_text() == "original"


@pytest.mark.parametrize(
    "raw", [b'{"version":1,"version":1}', b'{"version":NaN}', b"[" * 17 + b"0" + b"]" * 17]
)
def test_strict_evidence_parser(bundle, raw):
    with pytest.raises(HistoricalRecoveryError):
        recover(bundle, evidence=raw)
    assert not (bundle.directory / "recovered").exists()


def test_evidence_size_bound_before_decryption(bundle, monkeypatch):
    import camoufox_pm.sync.recovery_archive as module

    monkeypatch.setattr(module, "MAX_EVIDENCE_BYTES", 10)
    with pytest.raises(HistoricalRecoveryError):
        recover(bundle)
    assert not (bundle.directory / "recovered").exists()


def test_cli_prompts_secret_not_argv_and_prints_only_public_result(bundle, monkeypatch, capsys):
    import camoufox_pm.sync.recovery_archive as module

    package_file = bundle.directory / "package.bin"
    evidence_file = bundle.directory / "evidence.json"
    package_file.write_bytes(bundle.package)
    evidence_file.write_bytes(bundle.evidence)
    monkeypatch.setattr(module.getpass, "getpass", lambda prompt: bundle.secret)
    result = module.main(
        [
            "--package",
            str(package_file),
            "--evidence",
            str(evidence_file),
            "--ciphertext",
            str(bundle.ciphertext),
            "--output",
            str(bundle.directory / "recovered"),
            "--browser-build",
            "152-test",
            "--os",
            "linux",
            "--architecture",
            "x86_64",
            "--acknowledge-historical-fork",
        ]
    )
    assert result == 0
    output = capsys.readouterr().out
    assert "Historical archive verified" in output
    assert bundle.secret not in output and "private metadata" not in output


def test_symlink_destination_cannot_escape_or_delete_existing_data(bundle):
    destination = bundle.directory / "recovered"
    other = bundle.directory / "other"
    create_private_directory(other)
    (other / "keep").write_text("keep")
    try:
        destination.symlink_to(other, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation unavailable")
    with pytest.raises(HistoricalRecoveryError):
        recover(bundle)
    assert (other / "keep").read_text() == "keep"


def test_non_genesis_chain_needs_all_ancestors(bundle):
    evidence = json.loads(bundle.evidence)
    evidence["memberships"][0]["membership"]["epoch"] = 2
    evidence["manifest"]["membership_epoch"] = 2
    with pytest.raises(HistoricalRecoveryError):
        recover(bundle, evidence=json.dumps(evidence).encode())
    assert not (bundle.directory / "recovered").exists()
