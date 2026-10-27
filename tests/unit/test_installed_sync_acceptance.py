"""Validate acceptance trust provisioning without launching/building browsers."""

import os
import ssl
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography import x509
from scripts import installed_sync_acceptance as harness


def diagnostic():
    return {
        "version": 1,
        "area": "sync",
        "stage": "execute",
        "category": "invalid_state",
        "site": "core.profile_operations",
        "line": 64,
    }


@pytest.mark.parametrize("invalid", [False, True])
def test_installed_error_retains_only_validated_operation_evidence(invalid):
    evidence = diagnostic()
    if invalid:
        evidence["password"] = "private-credential"
    payload = {
        "detail": "Synchronization operation failed; inspect its state and retry",
        "error": {"details": [{"diagnostic": evidence}]},
        "request": "private-profile-path-and-url",
    }
    error = harness.installed_api_error(SimpleNamespace(status=409, json=lambda: payload))
    assert error.category == "runtime_failed"
    assert error.diagnostic == (None if invalid else diagnostic())
    assert "private-" not in str(error.__dict__)


def test_installed_missing_profile_probe_does_not_replay_or_change_profiles():
    calls = []
    pages = []

    def call(path):
        calls.append(("GET", path))
        return {"profiles": []}

    def post(url, **kwargs):
        calls.append(("POST", url))
        assert url == f"http://local/api/v1/sync/profiles/{harness.DIAGNOSTIC_PROFILE_ID}/acquire"
        assert kwargs["data"] == {} and kwargs["headers"] == {"Origin": "http://local"}
        return SimpleNamespace(
            status=409,
            headers={"cache-control": "no-store"},
            json=lambda: {
                "detail": "Synchronization operation failed; inspect its state and retry",
                "error": {"details": [{"diagnostic": diagnostic()}]},
            },
        )

    device = object.__new__(harness.Device)
    device.vault_kind = "native"
    device.origin = "http://local"
    device.call = call
    device.page = SimpleNamespace(goto=pages.append)
    device.context = SimpleNamespace(request=SimpleNamespace(post=post))
    assert device.check_runtime_diagnostic() == diagnostic()
    assert [verb for verb, _path in calls] == ["GET", "GET", "POST", "GET"]
    assert pages == ["about:blank", "http://local/"]


@pytest.mark.parametrize(
    ("detail", "category"),
    [
        ("Another synchronization ceremony is in progress", "admission_busy"),
        ("Synchronization operation failed; inspect its state and retry", "runtime_failed"),
        (
            "Synchronization ceremony failed; check setup and unlock credentials",
            "ceremony_failed",
        ),
        ("Synchronization runtime is unavailable", "runtime_unavailable"),
        ("private synthetic profile value must never escape", "unclassified"),
        ({"private": "synthetic secret"}, "unclassified"),
    ],
)
def test_installed_api_failure_exports_only_allowlisted_category(detail, category):
    from types import SimpleNamespace

    response = SimpleNamespace(status=409, json=lambda: {"detail": detail})
    error = harness.installed_api_error(response)
    assert error.status == 409
    assert error.category == category
    assert "synthetic" not in str(error)
    assert "synthetic" not in repr(error.__dict__)


def test_installed_api_failure_does_not_export_invalid_response_body():
    from types import SimpleNamespace

    def invalid_json():
        raise ValueError("synthetic private response")

    error = harness.installed_api_error(SimpleNamespace(status=503, json=invalid_json))
    assert error.category == "unclassified"
    assert "synthetic" not in repr(error.__dict__)


@pytest.mark.parametrize("accepted", [False, True])
def test_profile_ui_operation_observes_response_without_replaying_mutation(accepted, capsys):
    from contextlib import contextmanager, nullcontext
    from types import SimpleNamespace

    mutations = []
    reply = SimpleNamespace(
        ok=accepted,
        status=200 if accepted else 409,
        json=lambda: {"detail": "Synchronization operation failed; inspect its state and retry"},
    )

    @contextmanager
    def response(predicate, *, timeout):
        assert timeout == 180000
        for method, expected in (("GET", False), ("POST", True)):
            assert (
                predicate(
                    SimpleNamespace(
                        request=SimpleNamespace(method=method),
                        url="http://127.0.0.1/api/v1/sync/profiles/synthetic/trash",
                    )
                )
                is expected
            )
        yield SimpleNamespace(value=reply)

    device = object.__new__(harness.Device)
    device.page = SimpleNamespace(expect_response=response)
    with nullcontext() if accepted else pytest.raises(harness.InstalledAPIError):
        with device.checked_profile_operation():
            mutations.append("one native confirmation")
    assert mutations == ["one native confirmation"]
    output = capsys.readouterr().out
    assert f"HTTP {reply.status}" in output and "synthetic" not in output


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

    monkeypatch.setattr(
        harness, "delete_native_slot", lambda namespace, slot: removed.append((namespace, slot))
    )
    harness.cleanup_native_slots(tmp_path)
    assert set(removed) == {(owner, owner), (owner, first), (owner, second)}
    assert len(removed) == 3


@pytest.mark.parametrize("mutating", [False, True])
def test_installed_api_retries_only_read_only_explicit_busy_response(mutating, monkeypatch):
    from types import SimpleNamespace

    replies = [
        SimpleNamespace(
            ok=False,
            status=409,
            json=lambda: {"detail": "Another synchronization ceremony is in progress"},
        ),
        SimpleNamespace(ok=True, status=200, json=lambda: {"phase": "ready"}),
    ]
    calls = []

    def fetch(*args, **kwargs):
        calls.append(kwargs)
        return replies.pop(0)

    device = object.__new__(harness.Device)
    device.context = SimpleNamespace(request=SimpleNamespace(fetch=fetch))
    device.origin = "http://127.0.0.1:12345"
    monkeypatch.setattr(harness.time, "sleep", lambda _: None)
    if mutating:
        with pytest.raises(harness.InstalledAPIError) as error:
            device.call("/sync/enrollment", {})
        assert error.value.status == 409 and len(calls) == 1
    else:
        assert device.call("/sync/status") == {"phase": "ready"}
        assert len(calls) == 2


def test_installed_api_does_not_retry_key_failure(monkeypatch):
    from types import SimpleNamespace

    reply = SimpleNamespace(
        ok=False,
        status=409,
        json=lambda: {
            "detail": "Synchronization ceremony failed; check setup and unlock credentials"
        },
    )
    device = object.__new__(harness.Device)
    device.context = SimpleNamespace(request=SimpleNamespace(fetch=lambda *a, **k: reply))
    device.origin = "http://127.0.0.1:12345"
    monkeypatch.setattr(harness.time, "sleep", lambda _: pytest.fail("Key failure must not retry"))
    with pytest.raises(harness.InstalledAPIError):
        device.call("/sync/pairing/owner-code")


def test_macos_keychain_settings_restored_after_child_failure(monkeypatch):
    from scripts import run_with_ci_keychain as wrapper

    operations = []

    def security(*arguments):
        operations.append(arguments)
        if arguments == ("list-keychains", "-d", "user"):
            return '"/synthetic/login.keychain-db" "/synthetic/other.keychain-db"'
        if arguments == ("default-keychain", "-d", "user"):
            return '"/synthetic/login.keychain-db"'
        return ""

    def fail(*args, **kwargs):
        assert kwargs["env"]["CPM_CI_ISOLATED_KEYCHAIN"] == "1"
        raise RuntimeError("synthetic child failure")

    monkeypatch.setattr(wrapper.sys, "platform", "darwin")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(wrapper.sys, "argv", ["wrapper", "--", "synthetic-command"])
    monkeypatch.setattr(wrapper, "security", security)
    monkeypatch.setattr(wrapper.subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="synthetic child failure"):
        wrapper.main()
    assert operations[-3] == (
        "list-keychains",
        "-d",
        "user",
        "-s",
        "/synthetic/login.keychain-db",
        "/synthetic/other.keychain-db",
    )
    assert operations[-2] == (
        "default-keychain",
        "-d",
        "user",
        "-s",
        "/synthetic/login.keychain-db",
    )
    assert operations[-1][0] == "delete-keychain"
    assert operations[-1][1] == next(
        item[-1] for item in operations if item[0] == "create-keychain"
    )


def test_keychain_command_timeout_never_exposes_password(monkeypatch):
    from scripts import run_with_ci_keychain as wrapper

    def timeout(command, **kwargs):
        raise wrapper.subprocess.TimeoutExpired(command, 30)

    monkeypatch.setattr(wrapper.subprocess, "run", timeout)
    with pytest.raises(RuntimeError) as error:
        wrapper.security("unlock-keychain", "-p", "synthetic-secret")
    assert "synthetic-secret" not in str(error.value)
    assert error.value.__suppress_context__


@pytest.mark.parametrize("busy", [False, True])
def test_sync_observation_finishes_backup_before_one_shot_launch(monkeypatch, busy):
    calls = []

    def call(path, body):
        calls.append(path)
        if path == "/system/backups":
            return {"data": {"backups": [{"profile_id": "target"}]}}
        if path.endswith("/launch") and busy:
            raise harness.InstalledAPIError(409, "profile_busy")
        return {}

    monkeypatch.setattr(harness, "wait_for", lambda check, **kwargs: check())
    monkeypatch.setattr(
        harness.Probe, "reports", {"first": {"httpOnly": True, "authenticated": True}}
    )
    report = {}
    device = SimpleNamespace(call=call)
    if busy:
        with pytest.raises(harness.InstalledAPIError):
            harness.observe(device, "target", "http://local", "first", report)
    else:
        harness.observe(device, "target", "http://local", "first", report)
    assert calls[:2] == ["/system/backups", "/profiles/target/launch"]
    assert calls.count("/profiles/target/launch") == 1
    assert report["backup_checks"]["first"]["backup_preparation"]["prepared"] is True
    assert ("/browsers/close-all" in calls) is (not busy)


@pytest.mark.parametrize("status,category", [(409, "profile_busy"), (404, "unclassified")])
def test_profile_busy_classification_exports_no_profile_or_holder(status, category):
    response = SimpleNamespace(
        status=status,
        json=lambda: {
            "detail": "Profile SecretID is leased by another holder (private-host:123:slot)"
        },
    )
    error = harness.installed_api_error(response)
    assert error.category == category
    assert "SecretID" not in repr(error.__dict__)
    assert "private-host" not in repr(error.__dict__)


def test_acceptance_module_entrypoint_starts_without_pythonpath():
    import subprocess
    import sys

    environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, "-m", "scripts.installed_sync_acceptance", "--help"],
        cwd=harness.ROOT,
        env=environment,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0
    assert b"--vault" in result.stdout and b"--output" in result.stdout
