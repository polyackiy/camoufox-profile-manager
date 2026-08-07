"""Tests for Excel export/import."""

import pytest

from camoufox_pm.core.excel_manager import ExcelManager


@pytest.mark.asyncio
async def test_export_then_import_roundtrip(profile_manager):
    await profile_manager.create_profile(name="excel-1")
    await profile_manager.create_profile(name="excel-2")

    excel_manager = ExcelManager(profile_manager)
    data = await excel_manager.export_profiles_to_excel()
    assert isinstance(data, bytes) and len(data) > 0

    result = await excel_manager.import_profiles_from_excel(data)
    # Import always creates fresh profiles, so both rows come back in.
    assert result["created_count"] == 2
    assert result["error_count"] == 0
