"""Bounded CI renderer preparation must expose cold drift and fail closed."""

import base64
import json
from contextlib import nullcontext

import pytest
from scripts import prepare_ci_windows_renderer as preflight

from tests.browser.support import LocalSites


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "hashes,expected_launches,cold_transition",
    [(["cold", "warm", "warm"], 3, True), (["warm", "warm"], 2, False)],
)
async def test_preflight_requires_last_two_matches_keeps_every_baseline_and_warns(
    monkeypatch, tmp_path, capsys, hashes, expected_launches, cold_transition
):
    calls = []

    async def capture(profile, url):
        calls.append(profile)
        assert url == "http://127.0.0.1:1234/"
        return (
            {"pngHash": hashes[len(calls) - 1], "engineVersion": "153.0"},
            "data:image/png;base64," + base64.b64encode(b"synthetic-png").decode(),
        )

    monkeypatch.setattr(preflight, "capture_native_canvas", capture)
    monkeypatch.setattr(
        preflight,
        "serve_local_sites",
        lambda: nullcontext(LocalSites("http://127.0.0.1:1234/", "http://localhost:1234/")),
    )
    result = await preflight.prepare_renderer(tmp_path)
    assert len(result) == len(calls) == expected_launches
    assert len(set(calls)) == expected_launches
    assert all(not profile.parent.exists() for profile in calls)
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["converged"] is True
    assert summary["coldTransition"] is cold_transition
    assert summary["hashes"] == hashes
    assert summary["maxLaunches"] == 3
    assert ("::warning::" in capsys.readouterr().out) is cold_transition
    for number in range(1, expected_launches + 1):
        assert (tmp_path / f"launch-{number}.png").read_bytes() == b"synthetic-png"
        assert (
            json.loads((tmp_path / f"launch-{number}.json").read_text())["pngHash"]
            == hashes[number - 1]
        )


@pytest.mark.asyncio
async def test_nonconvergent_renderer_hard_fails_after_three_and_retains_evidence(
    monkeypatch, tmp_path
):
    calls = []

    async def capture(profile, _url):
        calls.append(profile)
        return (
            {"pngHash": str(len(calls)), "engineVersion": "153.0"},
            "data:image/png;base64,c3ludGhldGlj",
        )

    monkeypatch.setattr(preflight, "capture_native_canvas", capture)
    monkeypatch.setattr(
        preflight, "serve_local_sites", lambda: nullcontext(LocalSites("local", "local"))
    )
    with pytest.raises(preflight.RendererNotStable):
        await preflight.prepare_renderer(tmp_path)
    assert len(calls) == 3
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["converged"] is False and summary["hashes"] == ["1", "2", "3"]
    assert (tmp_path / "launch-3.png").is_file()


def test_native_launch_error_returns_failure_without_dumping_private_details(
    monkeypatch, tmp_path, capsys
):
    async def fail(_directory):
        raise RuntimeError("synthetic-private-error-detail")

    monkeypatch.setattr(preflight, "prepare_renderer", fail)
    monkeypatch.setattr(preflight.sys, "platform", "win32")
    monkeypatch.setattr(preflight.sys, "argv", ["preflight", "--output", str(tmp_path)])
    assert preflight.main() == 1
    message = capsys.readouterr().err
    assert "::error::" in message
    assert "synthetic-private-error-detail" not in message
