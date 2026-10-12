"""Shared stage-two boundaries: authentication, aliases and secret-safe errors."""

import pytest

from camoufox_pm.config import get_settings


@pytest.mark.parametrize("prefix", ["/api", "/api/v1"])
@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("GET", "/templates", None),
        ("GET", "/extensions", None),
        ("GET", "/saved-filters", None),
        ("POST", "/profiles/example/cookies/preview", {"format": "json", "text": "[]"}),
        (
            "POST",
            "/profiles/example/cookies/import",
            {
                "format": "json",
                "text": "[]",
                "mode": "merge",
                "row_version": 0,
            },
        ),
        ("GET", "/profiles/example/cookies/export?format=json&row_version=0", None),
        ("PUT", "/profiles/example/extensions", {"extension_ids": [], "row_version": 0}),
        (
            "POST",
            "/profiles/tags",
            {
                "profiles": [{"id": "example", "row_version": 0}],
                "add": ["tag"],
                "remove": [],
            },
        ),
    ],
)
async def test_stage_two_requires_auth_on_both_prefixes(
    client, monkeypatch, prefix, method, path, payload
):
    monkeypatch.setenv("CPM_API_KEY", "stage-two-test-key")
    get_settings.cache_clear()
    try:
        response = await client.request(method, prefix + path, json=payload)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("prefix", ["/api", "/api/v1"])
async def test_cookie_validation_never_reflects_sensitive_input(client, prefix):
    marker = "private-cookie-content-must-not-be-reflected"
    response = await client.post(
        prefix + "/profiles/example/cookies/import",
        json={"format": "json", "text": {"secret": marker}, "row_version": 0, "mode": "merge"},
    )
    assert response.status_code == 422
    assert marker not in response.text
    assert all(
        "input" not in error and "ctx" not in error for error in response.json()["error"]["details"]
    )
