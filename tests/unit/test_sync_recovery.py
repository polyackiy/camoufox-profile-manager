"""Recovery tests cross a real isolated interpreter boundary; no keyring needed."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from camoufox_pm.sync import recovery
from camoufox_pm.sync.identity import bootstrap, canonical_bytes, digest, encode_key
from camoufox_pm.sync.recovery import (
    MAX_REQUEST,
    RecoveryError,
    create_recovery,
    open_owner_recovery,
    recovery_worker_main,
    verify_recovery_isolated,
)
from camoufox_pm.sync.vault import open_recovery, seal_recovery


def prepared():
    root, secrets, membership = bootstrap("https://relay.example")
    package, secret = create_recovery(root, secrets.authority)
    return root, secrets, membership, package, secret


def test_recovery_payload_contains_only_owner_authority():
    root, secrets, membership, package, secret = prepared()
    recovered_root, recovered_authority = open_owner_recovery(package, secret)
    assert recovered_root == root
    assert recovered_authority.to_bytes() == secrets.authority.to_bytes()
    payload = json.loads(open_recovery(package, secret))
    assert set(payload) == {"version", "root", "authority"}
    assert payload["authority"] == encode_key(secrets.authority.to_bytes())
    assert membership.membership.devices[0].device_id.encode() not in open_recovery(package, secret)
    assert encode_key(secrets.device.to_bytes()) not in str(payload)
    assert secret.encode() not in package
    assert root.authority_public.encode() not in package


def test_actual_fresh_process_does_not_use_parent_unlocked_keys(monkeypatch):
    root, secrets, membership, package, secret = prepared()
    del secrets, membership

    def forbidden(*args, **kwargs):
        raise AssertionError("parent process private key access")

    monkeypatch.setattr(recovery, "open_owner_recovery", forbidden)
    # Child independently imports the trusted module and opens only supplied package.
    verify_recovery_isolated(package, secret, root)


def test_actual_fresh_process_rejects_wrong_root_and_tamper():
    root, _, _, package, secret = prepared()
    other, _, _ = bootstrap("https://relay.example")
    with pytest.raises(RecoveryError):
        verify_recovery_isolated(package, secret, other)
    obj = json.loads(package)
    obj["ciphertext"] = ("A" if obj["ciphertext"][0] != "A" else "B") + obj["ciphertext"][1:]
    with pytest.raises(RecoveryError):
        verify_recovery_isolated(json.dumps(obj).encode(), secret, root)
    with pytest.raises(RecoveryError):
        verify_recovery_isolated(package, "A" * 43, root)


def test_binding_and_payload_allowlist():
    root, secrets, _, package, secret = prepared()
    other, other_secrets, _ = bootstrap("https://relay.example")
    with pytest.raises(RecoveryError):
        create_recovery(root, other_secrets.authority)
    payload = json.loads(open_recovery(package, secret))
    for changed in (
        payload | {"root": other.model_dump()},
        payload | {"device": "forbidden"},
        payload | {"version": True},
        payload | {"authority": encode_key(secrets.device.to_bytes())},
    ):
        bad_package, bad_secret = seal_recovery(json.dumps(changed).encode())
        with pytest.raises(RecoveryError):
            open_owner_recovery(bad_package, bad_secret)


def test_process_has_no_secret_argv_env_or_file_transport(monkeypatch):
    root, _, _, package, secret = prepared()
    monkeypatch.setenv("SYNC_TEST_SECRET", secret)
    original_run = subprocess.run
    calls = []

    def inspect(*args, **kwargs):
        calls.append((args, kwargs))
        assert secret not in repr(args)
        assert secret not in repr(kwargs["env"])
        assert "HOME" not in kwargs["env"]
        assert "-I" in args[0]
        assert kwargs["stderr"] is subprocess.DEVNULL
        assert json.loads(kwargs["input"])["secret"] == secret
        return original_run(*args, **kwargs)

    monkeypatch.setattr(recovery.subprocess, "run", inspect)
    verify_recovery_isolated(package, secret, root)
    assert len(calls) == 1


def test_timeout_is_killed_and_sanitized(monkeypatch):
    root, _, _, package, secret = prepared()
    monkeypatch.setattr(recovery, "VERIFY_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(
        recovery,
        "_worker_command",
        lambda: [sys.executable, "-I", "-c", "import time; time.sleep(60)"],
    )
    with pytest.raises(RecoveryError) as error:
        verify_recovery_isolated(package, secret, root)
    assert str(error.value) == "Offline recovery verification failed"
    assert error.value.__cause__ is None
    assert secret not in repr(error.value)


@pytest.mark.parametrize("returncode,stdout", [(1, b""), (0, b"junk"), (0, b"a" * 10000)])
def test_subprocess_output_is_exact_and_errors_redacted(monkeypatch, returncode, stdout):
    root, _, _, package, secret = prepared()
    monkeypatch.setattr(
        recovery.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=returncode, stdout=stdout),
    )
    with pytest.raises(RecoveryError) as error:
        verify_recovery_isolated(package, secret, root)
    assert secret not in str(error.value)


def test_oversized_input_rejected_before_process(monkeypatch):
    root, _, _, _, secret = prepared()

    def forbidden(*args, **kwargs):
        raise AssertionError("must not spawn")

    monkeypatch.setattr(recovery.subprocess, "run", forbidden)
    with pytest.raises(RecoveryError):
        verify_recovery_isolated(b"x" * MAX_REQUEST, secret, root)


def test_worker_bounded_stdin_and_no_error_output(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"x" * (MAX_REQUEST + 1))))
    assert recovery_worker_main() == 1
    assert capsys.readouterr() == ("", "")
    monkeypatch.setattr(
        sys,
        "stdin",
        SimpleNamespace(buffer=io.BytesIO(b'{"package":"sensitive","secret":"invalid"}')),
    )
    assert recovery_worker_main() == 1
    assert capsys.readouterr() == ("", "")


def test_worker_emits_only_public_digest(monkeypatch, capsys):
    root, _, _, package, secret = prepared()
    request = recovery._VerificationRequest(package=encode_key(package), secret=secret)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(canonical_bytes(request))))
    assert recovery_worker_main() == 0
    assert capsys.readouterr() == (digest(root) + "\n", "")


def test_frozen_command_uses_only_internal_flag(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert recovery._worker_command() == [sys.executable, recovery.RECOVERY_WORKER_FLAG]


def test_windowed_worker_uses_only_inherited_pipes(monkeypatch):
    root, _, _, package, secret = prepared()
    request = recovery._VerificationRequest(package=encode_key(package), secret=secret)

    class InspectablePipe(io.BytesIO):
        def close(self):
            pass

    source = InspectablePipe(canonical_bytes(request))
    destination = InspectablePipe()
    calls = []

    def pipe(input_pipe):
        calls.append(input_pipe)
        return source if input_pipe else destination

    monkeypatch.setattr(sys, "stdin", None)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(recovery, "_windows_pipe", pipe)
    assert recovery_worker_main() == 0
    assert calls == [True, False]
    assert destination.getvalue() == (digest(root) + "\n").encode()


def test_windowed_worker_missing_pipe_fails_closed(monkeypatch):
    def unavailable(input_pipe):
        raise OSError("no inherited pipe")

    monkeypatch.setattr(sys, "stdin", None)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(recovery, "_windows_pipe", unavailable)
    assert recovery_worker_main() == 1
