"""Integration tests for the 1.0 API contract: versioned paths, one error shape,
and a schema a client can be generated from.

The older tests in this directory deliberately keep calling the unversioned
``/api`` paths — they double as regression coverage for the alias.
"""

import pytest

from camoufox_pm.main import app


@pytest.mark.asyncio
async def test_v1_is_canonical_and_the_legacy_alias_serves_the_same_data(client):
    created = await client.post("/api/v1/profiles", json={"name": "versioned"})
    assert created.status_code == 201
    profile_id = created.json()["id"]

    via_v1 = await client.get(f"/api/v1/profiles/{profile_id}")
    via_alias = await client.get(f"/api/profiles/{profile_id}")
    assert via_v1.status_code == via_alias.status_code == 200
    assert via_v1.json() == via_alias.json()


@pytest.mark.asyncio
async def test_errors_share_one_shape(client):
    response = await client.get("/api/v1/profiles/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"
    assert isinstance(body["error"]["message"], str)
    # The legacy mirror, kept until 1.0.
    assert body["detail"] == body["error"]["message"]


@pytest.mark.asyncio
async def test_request_validation_errors_use_the_same_shape(client):
    """FastAPI's own validation errors must not be the one differently-shaped error."""
    response = await client.post("/api/v1/profiles", json={"name": ""})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert isinstance(body["detail"], str), "detail used to be a list here"
    assert isinstance(body["error"]["details"], list)
    assert body["error"]["details"][0]["loc"]


@pytest.mark.asyncio
async def test_late_validation_errors_use_the_same_shape(client):
    """browser_settings is free-form on the way in, so it fails after FastAPI."""
    created = await client.post("/api/v1/profiles", json={"name": "late"})
    response = await client.put(
        f"/api/v1/profiles/{created.json()['id']}",
        json={"browser_settings": {"hardware_concurrency": "many"}},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert isinstance(body["detail"], str)
    assert isinstance(body["error"]["details"], list)


@pytest.mark.asyncio
async def test_auth_failures_use_the_same_shape(client, monkeypatch):
    from camoufox_pm.config import get_settings

    monkeypatch.setenv("CPM_API_KEY", "top-secret")
    get_settings.cache_clear()
    try:
        response = await client.get("/api/v1/profiles")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_health_reports_unhealthy_as_503(client, monkeypatch):
    """A healthy-looking 200 would hide the failure from probes that only read
    the status code."""
    from camoufox_pm.api import dependencies

    monkeypatch.setattr(dependencies, "_profile_manager", None)
    response = await client.get("/health")
    assert response.status_code == 503
    assert response.json()["status"] == "unhealthy"
    assert response.json()["database"] == "disconnected"


def test_openapi_exposes_only_versioned_paths_with_stable_operation_ids():
    schema = app.openapi()
    api_paths = [path for path in schema["paths"] if path.startswith("/api")]
    assert api_paths, "the API disappeared from its own schema"
    assert all(path.startswith("/api/v1/") for path in api_paths), (
        "legacy alias paths must stay out of the schema so a generated client "
        "sees each operation once"
    )

    operation_ids = []
    for path, methods in schema["paths"].items():
        for method, operation in methods.items():
            operation_ids.append(operation["operationId"])
            assert operation.get("summary"), f"{method.upper()} {path} has no summary"
    assert len(operation_ids) == len(set(operation_ids)), "operation ids must be unique"


def test_deprecations_are_marked_in_the_schema():
    schema = app.openapi()
    update_request = schema["components"]["schemas"]["ProfileUpdateRequest"]
    assert update_request["properties"]["browser_os"].get("deprecated") is True
    launch_response = schema["components"]["schemas"]["ProfileLaunchResponse"]
    assert launch_response["properties"]["browser_session_id"].get("deprecated") is True
