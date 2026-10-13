"""Operator credentials never need to appear in argv or logs."""

import os
import sys

import pytest

from camoufox_pm.core.update_permissions import protect_path
from camoufox_pm.sync.relay_cli import main, read_registration_token


def test_generate_private_token_then_read(tmp_path, monkeypatch, capsys):
    path = tmp_path / "credential"
    monkeypatch.setattr(sys, "argv", ["relay", "--generate-registration-token", str(path)])
    main()
    token = read_registration_token(path)
    assert len(token) == 43
    assert token not in capsys.readouterr().out
    if os.name != "nt":
        assert path.stat().st_mode & 0o077 == 0
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
    assert read_registration_token(path) == token


@pytest.mark.parametrize("payload", [b"short", b"x" * 1025, b"x" * 40 + b" " + b"y" * 40])
def test_bad_credential(tmp_path, payload):
    path = tmp_path / "credential"
    path.write_bytes(payload)
    protect_path(path)
    with pytest.raises(ValueError):
        read_registration_token(path)


def test_development_cannot_bind_public(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "relay",
            "--origin",
            "http://127.0.0.1",
            "--registration-token-file",
            "unused",
            "--development-loopback",
            "--host",
            "0.0.0.0",
        ],
    )
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2


def test_maintenance_cli_bounded_without_registration(monkeypatch, capsys, tmp_path):
    import camoufox_pm.sync.relay as relay

    calls = []

    def cleanup(directory, *, limit):
        calls.append((directory, limit))
        return {"examined": 2, "expired": 1, "skipped": 1, "failed": 0}

    monkeypatch.setattr(relay, "expire_uncommitted_uploads", cleanup)
    monkeypatch.setattr(
        sys,
        "argv",
        ["relay", "--data", str(tmp_path), "--expire-uploads", "--maintenance-limit", "2"],
    )
    main()
    assert calls == [(tmp_path, 2)]
    assert "expired=1" in capsys.readouterr().out
