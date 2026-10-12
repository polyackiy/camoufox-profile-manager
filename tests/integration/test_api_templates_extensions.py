"""Complete extension upload/assignment and fresh-template API contracts."""

import pytest

from tests.unit.test_extension_library import xpi


@pytest.mark.asyncio
async def test_upload_assign_template_fresh_profile_and_delete_keep_snapshots(client):
    uploaded = await client.post(
        "/api/v1/extensions", files={"file": ("proof.xpi", xpi(), "application/x-xpinstall")}
    )
    assert uploaded.status_code == 201, uploaded.text
    extension = uploaded.json()["data"]
    assert "xpi" not in extension and extension["permissions"] == ["http://127.0.0.1/*", "storage"]
    source = (
        await client.post(
            "/api/v1/profiles",
            json={"name": "source", "generate_fingerprint": False, "tags": ["work"]},
        )
    ).json()
    assigned = await client.put(
        f"/api/v1/profiles/{source['id']}/extensions",
        json={"row_version": source["row_version"], "extension_ids": [extension["id"]]},
    )
    assert assigned.status_code == 200, assigned.text
    assert (
        await client.put(
            f"/api/v1/profiles/{source['id']}/extensions",
            json={"row_version": 0, "extension_ids": []},
        )
    ).status_code == 409
    template = await client.post(
        "/api/v1/templates/from-profile",
        json={
            "name": "Reusable",
            "profile_id": source["id"],
            "profile_row_version": assigned.json()["data"]["row_version"],
        },
    )
    assert template.status_code == 201, template.text
    template = template.json()["data"]
    assert (
        await client.delete(f"/api/v1/extensions/{extension['id']}?row_version=0")
    ).status_code == 200
    first = await client.post(
        f"/api/v1/templates/{template['id']}/profiles", json={"row_version": 0, "name": "fresh"}
    )
    assert first.status_code == 201, first.text
    profile = first.json()["data"]
    assert (
        profile["proxy_config"] is None
        and profile["fingerprint"] is None
        and profile["tags"] == ["work"]
    )
    assert (await client.get(f"/api/v1/profiles/{profile['id']}/extensions")).json()["data"][
        "extensions"
    ]
    assert (
        await client.patch(
            f"/api/v1/templates/{template['id']}", json={"row_version": 0, "notes": "New defaults"}
        )
    ).status_code == 200
    assert (
        await client.delete(f"/api/v1/templates/{template['id']}?row_version=0")
    ).status_code == 409
    assert (
        await client.delete(f"/api/v1/templates/{template['id']}?row_version=1")
    ).status_code == 200


@pytest.mark.asyncio
async def test_invalid_upload_and_template_fields_create_nothing(client):
    bad = await client.post(
        "/api/v1/extensions",
        files={"file": ("unsafe.xpi", xpi(extra={"../outside": "bad"}), "application/zip")},
    )
    assert bad.status_code == 422
    for request in (
        {"name": " "},
        {"name": "Secret", "proxy_config": {"password": "private-value"}},
        {"name": "Bad", "browser_settings": {"cookies": [{"secret": "private-value"}]}},
    ):
        result = await client.post("/api/v1/templates", json=request)
        assert result.status_code == 422
        assert "private-value" not in result.text
    assert (await client.get("/api/v1/extensions")).json()["data"]["extensions"] == []
    assert (await client.get("/api/v1/templates")).json()["data"]["templates"] == []
