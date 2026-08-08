"""Integration tests for the spreadsheet endpoints.

Bulk editing goes out through one endpoint and comes back through the other, so
these check the pair over HTTP: the file the browser is handed, and what the
server does with a file it should refuse.
"""

import pytest

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.mark.asyncio
async def test_the_export_is_served_as_a_spreadsheet_download(client):
    await client.post("/api/profiles", json={"name": "a"})

    response = await client.get("/api/profiles/export/excel")

    assert response.status_code == 200
    assert response.headers["content-type"] == XLSX
    assert "camoufox_profiles.xlsx" in response.headers["content-disposition"]
    assert response.content[:2] == b"PK", "an xlsx is a zip"


@pytest.mark.asyncio
async def test_an_exported_sheet_can_be_imported_again(client):
    await client.post("/api/profiles", json={"name": "a"})
    await client.post("/api/profiles", json={"name": "b"})
    exported = await client.get("/api/profiles/export/excel")

    imported = await client.post(
        "/api/profiles/import/excel",
        files={"file": ("profiles.xlsx", exported.content, XLSX)},
    )

    assert imported.status_code == 200
    body = imported.json()
    assert body["success"] is True
    assert body["data"]["created_count"] == 2
    assert body["data"]["error_count"] == 0
    assert (await client.get("/api/profiles")).json()["total"] == 4


@pytest.mark.asyncio
async def test_a_file_that_is_not_a_spreadsheet_is_refused(client):
    response = await client.post(
        "/api/profiles/import/excel",
        files={"file": ("profiles.csv", b"name,group\na,b\n", "text/csv")},
    )

    assert response.status_code == 400
    assert (await client.get("/api/profiles")).json()["total"] == 0


@pytest.mark.asyncio
async def test_a_corrupt_workbook_is_reported_rather_than_crashing_the_request(client):
    """The upload is named .xlsx but is not one. The user gets told what went
    wrong; a 500 would look like the server's fault."""
    response = await client.post(
        "/api/profiles/import/excel",
        files={"file": ("profiles.xlsx", b"not really a workbook", XLSX)},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["data"]["created_count"] == 0
    assert body["data"]["errors"]
