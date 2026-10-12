"""Opt-in native Firefox control for Windows machine-level canvas diagnostics.

This is not a product warmup: the control retains exact restart equality and
its failure artifacts, then CI runs the unchanged manager test independently.
The packaged Playwright engine is Firefox153, not Camoufox's Gecko156; record
that limitation alongside the result.
"""

import hashlib
import os
import sys

import pytest
from playwright.async_api import async_playwright

from tests.browser.test_fingerprint_stability import (
    CANVAS_DIAGNOSTIC,
    canvas_profile_diagnostic,
    save_canvas_diagnostic,
)


async def capture_native_canvas(profile_directory, url):
    """One independent native browser/driver launch of the fixed synthetic draw."""
    async with async_playwright() as playwright:
        context = await playwright.firefox.launch_persistent_context(
            user_data_dir=str(profile_directory),
            headless=True,
            locale="en-US",
            firefox_user_prefs={
                "privacy.baselineFingerprintingProtection": False,
                "privacy.fingerprintingProtection": False,
                "privacy.resistFingerprinting": False,
            },
        )
        try:
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            snapshot = await page.evaluate(CANVAS_DIAGNOSTIC)
            record = {
                **{key: value for key, value in snapshot.items() if key != "value"},
                "pngHash": hashlib.sha256(snapshot["value"].encode()).hexdigest()[:16],
                "engine": "Playwright packaged Firefox",
                "engineVersion": context.browser.version if context.browser else "unavailable",
            }
        finally:
            await context.close()
    record.update(canvas_profile_diagnostic(profile_directory))
    return record, snapshot["value"]


@pytest.mark.browser
@pytest.mark.asyncio
@pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("CPM_NATIVE_CANVAS_CONTROL") != "1",
    reason="Opt-in fresh Windows runner diagnostic with packaged Playwright Firefox",
)
async def test_native_firefox_canvas_survives_a_relaunch(tmp_path, local_sites):
    records = []
    hashes = []
    for number in (1, 2):
        record, value = await capture_native_canvas(tmp_path / "native", local_sites.first)
        record["phase"] = "initial" if number == 1 else "restart"
        records.append(record)
        hashes.append(record["pngHash"])
        directory = os.environ.get("CPM_CANVAS_DIAGNOSTIC_DIR")
        if directory:
            save_canvas_diagnostic(directory, number, value, record)
    assert hashes[0] == hashes[1], f"native Firefox canvas drifted: {records}"
