"""Integration tests for user login sessions and how they coexist with the
API key and the open loopback default.

The three configuration states, each of which must stay coherent:
(a) nothing configured — open, exactly as before user accounts existed;
(b) only ``CPM_API_KEY`` — machine clients unchanged;
(c) a user exists — a session cookie or the API key is required.
"""

import pytest

from camoufox_pm.api import dependencies
from camoufox_pm.api.routes import auth as auth_routes
from camoufox_pm.config import get_settings
from camoufox_pm.core import auth

PASSWORD = "a-decent-password"


@pytest.fixture(autouse=True)
def _fast_failures(monkeypatch):
    """The anti-brute-force delay is pure waiting; tests skip it."""
    monkeypatch.setattr(auth_routes, "FAILED_LOGIN_DELAY_SECONDS", 0.0)


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch):
    """Each test states its own key; none leaks in from the environment."""
    monkeypatch.delenv("CPM_API_KEY", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _create_user(username: str = "alice", password: str = PASSWORD) -> None:
    storage = dependencies.get_storage_manager()
    await storage.create_user(auth.new_user_id(), username, auth.hash_password(password))


@pytest.mark.asyncio
async def test_state_a_nothing_configured_stays_open(client):
    response = await client.get("/api/v1/profiles")
    assert response.status_code == 200

    session = await client.get("/api/v1/auth/session")
    assert session.json() == {
        "user_auth_enabled": False,
        "authenticated": False,
        "username": None,
    }


@pytest.mark.asyncio
async def test_state_b_api_key_alone_behaves_exactly_as_before(client, monkeypatch):
    monkeypatch.setenv("CPM_API_KEY", "top-secret")
    get_settings.cache_clear()

    assert (await client.get("/api/v1/profiles")).status_code == 401
    wrong = await client.get("/api/v1/profiles", headers={"X-API-Key": "nope"})
    assert wrong.status_code == 401
    right = await client.get("/api/v1/profiles", headers={"X-API-Key": "top-secret"})
    assert right.status_code == 200


@pytest.mark.asyncio
async def test_state_c_a_user_existing_closes_the_api(client):
    await _create_user()
    response = await client.get("/api/v1/profiles")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"

    # The login screen's probe stays reachable, or nobody could ever log in.
    session = await client.get("/api/v1/auth/session")
    assert session.status_code == 200
    assert session.json()["user_auth_enabled"] is True
    assert session.json()["authenticated"] is False


@pytest.mark.asyncio
async def test_login_grants_a_session_that_passes_the_guard(client):
    await _create_user()
    login = await client.post(
        "/api/v1/auth/login", json={"username": "alice", "password": PASSWORD}
    )
    assert login.status_code == 200
    assert login.json() == {
        "user_auth_enabled": True,
        "authenticated": True,
        "username": "alice",
    }

    set_cookie = login.headers["set-cookie"]
    assert "cpm_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie
    # Served over plain HTTP here, so Secure must not be set — it would make the
    # cookie undeliverable on the loopback default.
    assert "Secure" not in set_cookie

    # httpx carries the cookie forward like a browser would.
    assert (await client.get("/api/v1/profiles")).status_code == 200
    session = await client.get("/api/v1/auth/session")
    assert session.json()["username"] == "alice"


@pytest.mark.asyncio
async def test_wrong_password_and_unknown_user_are_indistinguishable(client):
    await _create_user()
    wrong_password = await client.post(
        "/api/v1/auth/login", json={"username": "alice", "password": "not-it-at-all"}
    )
    unknown_user = await client.post(
        "/api/v1/auth/login", json={"username": "nobody", "password": "not-it-at-all"}
    )
    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json() == unknown_user.json()


@pytest.mark.asyncio
async def test_logout_really_invalidates_the_session(client):
    await _create_user()
    login = await client.post(
        "/api/v1/auth/login", json={"username": "alice", "password": PASSWORD}
    )
    # Keep the raw token: the logout response clears the cookie jar, and the
    # point is that the server side is dead too, not just the browser copy.
    token = login.cookies["cpm_session"]

    assert (await client.get("/api/v1/profiles")).status_code == 200
    logout = await client.post("/api/v1/auth/logout")
    assert logout.status_code == 200

    assert (await client.get("/api/v1/profiles")).status_code == 401
    replayed = await client.get("/api/v1/profiles", headers={"Cookie": f"cpm_session={token}"})
    assert replayed.status_code == 401


@pytest.mark.asyncio
async def test_a_forged_cookie_does_not_pass(client):
    await _create_user()
    response = await client.get("/api/v1/profiles", headers={"Cookie": "cpm_session=" + "A" * 43})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_api_key_still_works_when_users_exist(client, monkeypatch):
    """Machine clients must not break the day a human account is created."""
    monkeypatch.setenv("CPM_API_KEY", "top-secret")
    get_settings.cache_clear()
    await _create_user()

    assert (await client.get("/api/v1/profiles")).status_code == 401
    keyed = await client.get("/api/v1/profiles", headers={"X-API-Key": "top-secret"})
    assert keyed.status_code == 200
    wrong = await client.get("/api/v1/profiles", headers={"X-API-Key": "nope"})
    assert wrong.status_code == 401


@pytest.mark.asyncio
async def test_no_response_ever_carries_the_password_hash(client):
    await _create_user()
    login = await client.post(
        "/api/v1/auth/login", json={"username": "alice", "password": PASSWORD}
    )
    session = await client.get("/api/v1/auth/session")
    for response in (login, session):
        assert "argon2" not in response.text
        assert PASSWORD not in response.text


@pytest.mark.asyncio
async def test_system_config_reports_user_auth(client):
    before = await client.get("/api/v1/system/config")
    assert before.json()["data"]["user_auth_enabled"] is False

    await _create_user()
    login = await client.post(
        "/api/v1/auth/login", json={"username": "alice", "password": PASSWORD}
    )
    assert login.status_code == 200
    after = await client.get("/api/v1/system/config")
    assert after.json()["data"]["user_auth_enabled"] is True
