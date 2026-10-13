"""Strict retention consent and cookie authorization at the HTTP boundary."""

# ruff: noqa: F811
from uuid import uuid4

import pytest

from camoufox_pm.api.routes import sync
from tests.unit.test_sync_api import api  # noqa: F401


@pytest.mark.parametrize(
    "operation,body,method",
    [
        ("list", {}, "retention_list"),
        (
            "schedule",
            {"revisions": [str(uuid4())], "acknowledge_cleanup": True},
            "retention_schedule",
        ),
        ("cancel", {"decision_id": str(uuid4())}, "retention_cancel"),
    ],
)
async def test_retention_routes_require_cookie_and_forward_named_fields(
    api, monkeypatch, operation, body, method
):
    client, _ = api
    calls = []

    async def invoke(request, user, action, local_id, **kwargs):
        calls.append((user, action, local_id, kwargs))
        return {"state": "scheduled"}

    monkeypatch.setattr(sync, "_runtime_call", invoke)
    path = "/sync/profiles/local1/retention/" + operation
    assert (await client.post(path, json=body)).status_code == 401
    assert not calls
    response = await client.post(
        path, json={**body, "password": "SECRET"}, headers={"cookie": "test=owner"}
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert calls == [("owner", method, "local1", {**body, "password": "SECRET"})]
    assert "SECRET" not in response.text


@pytest.mark.parametrize("consent", [False, 1, "true", None])
async def test_retention_rejects_coerced_or_missing_consent(api, consent):
    client, _ = api
    response = await client.post(
        "/sync/profiles/local1/retention/schedule",
        json={"revisions": [str(uuid4())], "acknowledge_cleanup": consent},
        headers={"cookie": "test=owner"},
    )
    assert response.status_code == 422
