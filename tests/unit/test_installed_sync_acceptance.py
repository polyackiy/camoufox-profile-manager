"""Validate acceptance trust provisioning without launching/building browsers."""

import os
import ssl
from pathlib import Path

import pytest
from cryptography import x509
from scripts import installed_sync_acceptance as harness


def test_ephemeral_certificate_is_short_lived_and_loopback_only(tmp_path):
    certificate, key = harness.tls_fixture(tmp_path)
    parsed = x509.load_pem_x509_certificate(certificate.read_bytes())
    names = parsed.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert [str(address) for address in names.get_values_for_type(x509.IPAddress)] == ["127.0.0.1"]
    assert not names.get_values_for_type(x509.DNSName)
    assert (parsed.not_valid_after_utc - parsed.not_valid_before_utc).total_seconds() <= 86700
    context = ssl.create_default_context(cafile=str(certificate))
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    context.load_cert_chain(certificate, key)


def test_trust_provisioning_changes_only_disposable_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(harness.sys, "platform", "linux")
    source = tmp_path / "release.pem"
    source.write_bytes(b"release CA store")
    installation = tmp_path / "installed"
    store = installation / "_internal" / "certifi" / "cacert.pem"
    store.parent.mkdir(parents=True)
    store.write_bytes(source.read_bytes())
    certificate, _ = harness.tls_fixture(tmp_path)
    harness.provision_test_trust(installation, certificate)
    assert store.read_bytes() == source.read_bytes() + b"\n" + certificate.read_bytes()
    assert source.read_bytes() == b"release CA store"


@pytest.mark.skipif(os.name == "nt", reason="Symlink privilege differs on Windows")
def test_trust_provisioning_refuses_external_store(tmp_path, monkeypatch):
    monkeypatch.setattr(harness.sys, "platform", "linux")
    outside = tmp_path / "outside.pem"
    outside.write_bytes(b"untouched")
    installation = tmp_path / "installed"
    directory = installation / "certifi"
    directory.mkdir(parents=True)
    (directory / "cacert.pem").symlink_to(outside)
    with pytest.raises(RuntimeError):
        harness.provision_test_trust(installation, Path("unused"))
    assert outside.read_bytes() == b"untouched"


def test_native_mode_never_sends_a_file_vault_password():
    device = object.__new__(harness.Device)
    device.password = "synthetic-account-password"
    device.vault_kind = "native"
    assert device.unlock_body == {}
    device.vault_kind = "encrypted_file"
    assert device.unlock_body == {"password": device.password}


def test_ci_keyring_session_isolates_credentials_but_preserves_browser_cache(tmp_path, monkeypatch):
    from scripts.run_with_ci_keyring import isolated_environment

    monkeypatch.setenv("XDG_CACHE_HOME", "/synthetic/browser-cache")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "synthetic-existing-session")
    monkeypatch.setenv("GNOME_KEYRING_CONTROL", "/synthetic/existing-keyring")
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "synthetic.fallback")
    environment = isolated_environment(tmp_path)
    assert environment["XDG_CACHE_HOME"] == "/synthetic/browser-cache"
    for key in ("DBUS_SESSION_BUS_ADDRESS", "GNOME_KEYRING_CONTROL", "PYTHON_KEYRING_BACKEND"):
        assert key not in environment
    for key in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR"):
        directory = Path(environment[key])
        assert directory.parent == tmp_path and directory.is_dir()
        if os.name != "nt":
            assert directory.stat().st_mode & 0o777 == 0o700
    assert os.environ["DBUS_SESSION_BUS_ADDRESS"] == "synthetic-existing-session"


def test_native_cleanup_targets_only_disposable_recorded_slots(tmp_path, monkeypatch):
    from camoufox_pm.sync import vault
    from camoufox_pm.sync.control import ControlStore

    owner = "11111111-1111-4111-8111-111111111111"
    first = "22222222-2222-4222-8222-222222222222"
    second = "33333333-3333-4333-8333-333333333333"
    for name, device, authority in (("owner", first, True), ("recipient", second, False)):
        directory = tmp_path / name / "data" / "sync-control"
        directory.parent.mkdir(parents=True)
        control = ControlStore(directory)
        control.create(
            "installation",
            {
                "vault_kind": "native",
                "root": {"owner_id": owner},
                "device_id": device,
                "authority": authority,
            },
        )
    # A pairing journal can repeat the installed slot: remove it only once.
    control.create(
        "ceremony/pairing",
        {
            "vault_kind": "native",
            "root": {"owner_id": owner},
            "request": {"device": {"device_id": second}},
        },
    )
    removed = []

    class RecordingVault:
        def __init__(self, namespace):
            self.namespace = namespace

        def delete(self, slot):
            removed.append((self.namespace, slot))

    monkeypatch.setattr(vault, "OSVault", RecordingVault)
    harness.cleanup_native_slots(tmp_path)
    assert set(removed) == {(owner, owner), (owner, first), (owner, second)}
    assert len(removed) == 3
