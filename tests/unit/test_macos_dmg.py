import subprocess

import pytest
from scripts import macos_dmg


def test_busy_image_creation_retries_without_publishing_partial_output(tmp_path, monkeypatch):
    output = tmp_path / "release.dmg"
    output.write_bytes(b"previous installer")
    attempts = []

    def create(command, **kwargs):
        candidate = tmp_path / command[-1]
        assert output.read_bytes() == b"previous installer"
        assert not candidate.exists()
        attempts.append(candidate)
        if len(attempts) == 1:
            candidate.write_bytes(b"partial")
            return subprocess.CompletedProcess(
                command, 1, "", "hdiutil: create failed - Resource busy"
            )
        candidate.write_bytes(b"new installer")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(macos_dmg.subprocess, "run", create)
    monkeypatch.setattr(macos_dmg.time, "sleep", lambda _: None)

    macos_dmg.create_dmg(tmp_path, output)

    assert len(attempts) == 2
    assert output.read_bytes() == b"new installer"
    assert all(not attempt.exists() for attempt in attempts)


def test_permanent_failure_leaves_the_existing_image_intact(tmp_path, monkeypatch):
    output = tmp_path / "release.dmg"
    output.write_bytes(b"previous installer")
    attempts = []

    def create(command, **kwargs):
        attempts.append(command)
        return subprocess.CompletedProcess(command, 1, "", "hdiutil: No space left on device")

    monkeypatch.setattr(macos_dmg.subprocess, "run", create)

    with pytest.raises(subprocess.CalledProcessError, match="returned non-zero"):
        macos_dmg.create_dmg(tmp_path, output)

    assert len(attempts) == 1
    assert output.read_bytes() == b"previous installer"
