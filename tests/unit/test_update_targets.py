from dataclasses import replace

import pytest

from camoufox_pm.core.update_targets import (
    UpdatePolicy,
    UpdateVerificationError,
    VerifiedTargetClient,
    validate_manifest,
)


@pytest.fixture
def policy():
    return UpdatePolicy(
        "manager",
        "1.2.0",
        "darwin",
        "arm64",
        "stable",
        1,
        "1.0",
        "135.0",
        "developer-id-test",
        "test.bundle",
        "14.0",
    )


@pytest.fixture
def custom(policy):
    return {
        "camoufox_pm": {
            "protocol": 1,
            "component": policy.component,
            "version": policy.version,
            "platform": policy.platform,
            "architecture": policy.architecture,
            "channel": policy.channel,
            "artifact": "manager.zip",
            "schema_min": 1,
            "schema_max": 1,
            "schema_result": 2,
            "recovery": "checkpoint-v1",
            "minimum_updater": "1.0",
            "browser_version": policy.browser_version,
            "publisher_policy": policy.publisher_policy,
            "product_id": policy.product_id,
            "minimum_os": "13.0",
        }
    }


def test_manifest_compatible(policy, custom):
    assert validate_manifest(custom, "manager.zip", policy)["schema_result"] == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("platform", "windows"),
        ("architecture", "x86_64"),
        ("publisher_policy", "other"),
        ("product_id", "other"),
        ("browser_version", "136.0"),
        ("version", "1.3.0"),
        ("schema_min", 2),
        ("schema_max", 0),
        ("schema_result", True),
        ("protocol", True),
        ("minimum_updater", "2.0"),
        ("minimum_os", "15.0"),
        ("recovery", "none"),
        ("artifact", "other.zip"),
    ],
)
def test_manifest_rejects_mismatch(policy, custom, field, value):
    custom["camoufox_pm"][field] = value
    with pytest.raises(UpdateVerificationError):
        validate_manifest(custom, "manager.zip", policy)


def test_unknown_manifest_fields_rejected(policy, custom):
    custom["camoufox_pm"]["extra"] = True
    with pytest.raises(UpdateVerificationError):
        validate_manifest(custom, "manager.zip", policy)


def test_stable_cannot_install_preview(policy, custom):
    custom["camoufox_pm"]["version"] = "1.2.0rc1"
    with pytest.raises(UpdateVerificationError):
        validate_manifest(custom, "manager.zip", replace(policy, version="1.2.0rc1"))


def test_missing_root_disables_download(tmp_path, policy):
    client = VerifiedTargetClient(
        tmp_path / "metadata",
        tmp_path / "stage",
        "https://example.org/metadata/",
        "https://example.org/targets/",
    )
    with pytest.raises(UpdateVerificationError, match="no trusted TUF root"):
        client.download("manager.zip", policy)
    assert not (tmp_path / "metadata").exists()


@pytest.mark.parametrize("path", ["../app", "/app", "a//b", "a/./b", "a%2fb", "a?b", "a\\b"])
def test_unsafe_target_path(tmp_path, policy, path):
    client = VerifiedTargetClient(
        tmp_path / "metadata",
        tmp_path / "stage",
        "https://example.org/metadata/",
        "https://example.org/targets/",
    )
    with pytest.raises(UpdateVerificationError, match="Unsafe"):
        client.download(path, policy)


@pytest.fixture
def signed_repository(tmp_path, monkeypatch, custom):
    """Use real ephemeral keys and the real TUF verifier with local transport."""
    tuf = pytest.importorskip("tuf.ngclient")
    from datetime import datetime, timedelta, timezone
    from urllib.parse import urlsplit

    from securesystemslib.signer import CryptoSigner
    from tuf.api.exceptions import DownloadHTTPError
    from tuf.api.metadata import Metadata, MetaFile, Root, Snapshot, TargetFile, Targets, Timestamp
    from tuf.ngclient.fetcher import FetcherInterface

    signer = CryptoSigner.generate_ed25519()
    future = datetime.now(timezone.utc) + timedelta(days=1)
    root = Metadata(Root(expires=future, consistent_snapshot=False))
    for role in ("root", "targets", "snapshot", "timestamp"):
        root.signed.add_key(signer.public_key, role)
    root.sign(signer)
    root_path = tmp_path / "trusted-root.json"
    root_path.write_bytes(root.to_bytes())
    from camoufox_pm.core.update_permissions import protect_path

    protect_path(root_path)
    content = b"real authenticated update bytes"
    target = TargetFile.from_data("manager.zip", content)
    target.unrecognized_fields["custom"] = custom
    targets = Metadata(Targets(expires=future, targets={"manager.zip": target}))
    targets.sign(signer)
    snapshot = Metadata(Snapshot(expires=future, meta={"targets.json": MetaFile(version=1)}))
    snapshot.sign(signer)
    timestamp = Metadata(Timestamp(expires=future, snapshot_meta=MetaFile(version=1)))
    timestamp.sign(signer)
    files = {
        "/metadata/targets.json": targets.to_bytes(),
        "/metadata/snapshot.json": snapshot.to_bytes(),
        "/metadata/timestamp.json": timestamp.to_bytes(),
        "/targets/manager.zip": content,
    }

    class LocalTransport(FetcherInterface):
        def _fetch(self, url):
            path = urlsplit(url).path
            if path not in files:
                raise DownloadHTTPError("missing local fixture", 404)
            yield files[path]

    real_updater = tuf.Updater
    monkeypatch.setattr(
        tuf,
        "Updater",
        lambda *args, **kwargs: real_updater(
            *args, **({**kwargs, "fetcher": kwargs.get("fetcher") or LocalTransport()})
        ),
    )
    client = VerifiedTargetClient(
        tmp_path / "metadata",
        tmp_path / "stage",
        "https://example.org/metadata/",
        "https://example.org/targets/",
        trusted_root=root_path,
    )
    return client, files, signer, timestamp, content


def test_real_tuf_download(signed_repository, policy):
    client, _, _, _, content = signed_repository
    verified = client.download("manager.zip", policy)
    assert verified.path.read_bytes() == content
    assert verified.length == len(content)
    assert "sha256" in verified.hashes
    assert (client.metadata_dir / "timestamp.json").exists()


@pytest.mark.parametrize("attack", ["target", "truncated", "oversized", "metadata", "expired"])
def test_real_tuf_rejects_tamper_and_expiry(signed_repository, policy, attack):
    from datetime import datetime, timedelta, timezone

    client, files, signer, timestamp, _ = signed_repository
    if attack == "target":
        files["/targets/manager.zip"] = b"altered malicious update bytes"
    elif attack == "truncated":
        files["/targets/manager.zip"] = b"partial"
    elif attack == "oversized":
        files["/targets/manager.zip"] = b"x" * 1000
    elif attack == "metadata":
        files["/metadata/timestamp.json"] = files["/metadata/timestamp.json"].replace(
            b'"version":1', b'"version":2'
        )
    else:
        timestamp.signed.expires = datetime.now(timezone.utc) - timedelta(days=1)
        timestamp.sign(signer)
        files["/metadata/timestamp.json"] = timestamp.to_bytes()
    with pytest.raises(UpdateVerificationError, match="TUF"):
        client.download("manager.zip", policy)
    assert not list(client.staging_dir.glob("verified-update-*"))


def test_real_tuf_preserves_metadata_replay_protection(signed_repository, policy):
    client, files, signer, timestamp, _ = signed_repository
    old_timestamp = timestamp.to_bytes()
    timestamp.signed.version = 2
    timestamp.sign(signer)
    files["/metadata/timestamp.json"] = timestamp.to_bytes()
    client.download("manager.zip", policy)
    persisted = (client.metadata_dir / "timestamp.json").read_bytes()
    files["/metadata/timestamp.json"] = old_timestamp
    with pytest.raises(UpdateVerificationError, match="TUF"):
        client.download("manager.zip", policy)
    assert (client.metadata_dir / "timestamp.json").read_bytes() == persisted


def test_real_tuf_root_key_rotation(signed_repository, policy):
    from securesystemslib.signer import CryptoSigner
    from tuf.api.metadata import Metadata

    client, files, old_signer, _, _ = signed_repository
    root = Metadata.from_file(str(client.trusted_root))
    root.signed.version = 2
    new_signer = CryptoSigner.generate_ed25519()
    for role in ("root", "targets", "snapshot", "timestamp"):
        root.signed.revoke_key(old_signer.public_key.keyid, role)
        root.signed.add_key(new_signer.public_key, role)
    root.sign(old_signer)
    root.sign(new_signer, append=True)
    files["/metadata/2.root.json"] = root.to_bytes()
    for name in ("targets", "snapshot", "timestamp"):
        metadata = Metadata.from_bytes(files[f"/metadata/{name}.json"])
        metadata.sign(new_signer)
        files[f"/metadata/{name}.json"] = metadata.to_bytes()
    assert client.download("manager.zip", policy).path.is_file()
    assert Metadata.from_file(str(client.metadata_dir / "root.json")).signed.version == 2


def test_real_tuf_download_is_bounded(signed_repository, policy):
    client, _, _, _, _ = signed_repository
    with pytest.raises(UpdateVerificationError, match="size limit"):
        client.download("manager.zip", replace(policy, max_target_bytes=1))
    assert not list(client.staging_dir.glob("verified-update-*"))


def test_external_cached_root_symlink_rejected(signed_repository, policy, tmp_path):
    import os

    if os.name == "nt":
        pytest.skip("Windows symlink privilege varies; ACL/reparse tests cover native rejection")
    from camoufox_pm.core.update_permissions import create_private_directory

    client, _, _, _, _ = signed_repository
    create_private_directory(client.metadata_dir)
    external = tmp_path / "outside-root.json"
    external.write_bytes(client.trusted_root.read_bytes())
    (client.metadata_dir / "root.json").symlink_to(external)
    with pytest.raises(UpdateVerificationError, match="Unsafe cached metadata"):
        client.download("manager.zip", policy)


@pytest.mark.parametrize(
    "text,windows,accepted",
    [
        ("root_history/1.root.json", False, True),
        ("root_history/42.root.json", True, True),
        (r"root_history\1.root.json", True, True),
        (r"root_history\1.root.json", False, False),
        (r"C:\root_history\1.root.json", True, False),
        (r"C:root_history\1.root.json", True, False),
        (r"\\server\root_history\1.root.json", True, False),
        (r"\root_history\1.root.json", True, False),
        ("/root_history/1.root.json", False, False),
        ("root_history/../1.root.json", True, False),
        ("root_history/./1.root.json", True, False),
        ("root_history//1.root.json", True, False),
        ("root_history/01.root.json", True, False),
        ("root_history/0.root.json", True, False),
        ("root_history/1.root.json:stream", True, False),
        ("other/1.root.json", True, False),
    ],
)
def test_native_root_history_link_lexical_contract(text, windows, accepted):
    from camoufox_pm.core.update_targets import _valid_root_history_link

    assert _valid_root_history_link(text, windows=windows) is accepted


def test_offline_reauthenticates_cached_metadata_and_archive(signed_repository, policy):
    client, files, _, _, content = signed_repository
    verified = client.download("manager.zip", policy)
    files.clear()  # The offline helper must work without network transport.
    again = client.download("manager.zip", policy, offline=True, cached_target=verified.path)
    assert again.path.read_bytes() == content
    verified.path.write_bytes(b"tampered")
    with pytest.raises(UpdateVerificationError):
        client.download("manager.zip", policy, offline=True, cached_target=verified.path)


def test_stable_manager_can_pin_exact_beta_browser(policy, custom):
    browser_policy = replace(
        policy, component="camoufox", version="156.0b2", browser_version="156.0b2"
    )
    custom["camoufox_pm"].update(component="camoufox", version="156.0b2", browser_version="156.0b2")
    assert validate_manifest(custom, "manager.zip", browser_policy)["version"] == "156.0b2"
    manager_policy = replace(policy, browser_version="156.0b2")
    custom["camoufox_pm"].update(component="manager", version=policy.version)
    assert validate_manifest(custom, "manager.zip", manager_policy)["browser_version"] == "156.0b2"
