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
