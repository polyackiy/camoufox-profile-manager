"""Protocol alias persistence, invalid credentials and compatibility of old records."""

import json

import pytest
from cryptography.fernet import Fernet

from camoufox_pm.api.dependencies import get_profile_manager
from camoufox_pm.config import get_settings
from camoufox_pm.core.models import Profile, ProxyConfig
from tests.support.proxy_protocols import certificates, proxy_fixture


@pytest.mark.asyncio
@pytest.mark.parametrize("alias,canonical", [("socks4a", "socks4"), ("socks5h", "socks5")])
async def test_aliases_stay_canonical_in_library_and_profile_database(client, alias, canonical):
    library = await client.post(
        "/api/v1/proxies",
        json={"name": "Alias proxy", "type": alias, "server": "proxy.example:1080"},
    )
    assert library.status_code == 201 and library.json()["data"]["type"] == canonical
    profile = await client.post(
        "/api/v1/profiles",
        json={
            "name": "Alias profile",
            "generate_fingerprint": False,
            "proxy_config": {"type": alias, "server": "proxy.example:1080"},
        },
    )
    assert profile.status_code == 201 and profile.json()["proxy_config"]["type"] == canonical
    row = (
        get_profile_manager()
        .storage.db._connection.execute("SELECT proxy_config FROM profiles")
        .fetchone()[0]
    )
    assert json.loads(row)["type"] == canonical


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "protocol,username,password",
    [
        ("socks4", "private-user", "private-password"),
        ("socks4a", "private-user", None),
        ("socks5", "private-user", None),
        ("socks5h", "😀" * 64, "private-password"),
    ],
)
async def test_unsupported_auth_cannot_be_saved_or_echoed(client, protocol, username, password):
    proxy = {
        "type": protocol,
        "server": "proxy.example:1080",
        "username": username,
        "password": password,
    }
    library = await client.post("/api/v1/proxies", json={"name": "Invalid", **proxy})
    assert library.status_code == 422
    assert "private-user" not in library.text and "private-password" not in library.text
    created = await client.post(
        "/api/v1/profiles",
        json={"name": "Invalid", "generate_fingerprint": False, "proxy_config": proxy},
    )
    assert created.status_code in {400, 422}
    assert "private-user" not in created.text and "private-password" not in created.text
    assert not await get_profile_manager().storage.list_profiles()


@pytest.mark.asyncio
async def test_socks4_health_check_returns_safe_reachable_result_on_both_routes(
    client, tmp_path, monkeypatch
):
    from camoufox_pm.core import proxy_check

    certs = certificates(tmp_path)
    monkeypatch.setenv("SSL_CERT_FILE", str(certs[0]))
    monkeypatch.setattr(proxy_check, "locate", lambda ip: proxy_check.ProxyLocation(ip=ip))
    async with proxy_fixture("socks4", certs) as fixture:
        monkeypatch.setattr(
            proxy_check, "IP_ENDPOINTS", (f"https://cpm-origin.invalid:{fixture.https_port}/ip",)
        )
        proxy = {"type": "socks4a", "server": f"127.0.0.1:{fixture.proxy_port}"}
        entry = (
            await client.post("/api/v1/proxies", json={"name": "Test SOCKS4", **proxy})
        ).json()["data"]
        for prefix in ("/api", "/api/v1"):
            response = await client.post(f"{prefix}/proxies/{entry['id']}/check")
            assert (
                response.status_code == 200 and response.json()["data"]["last_check"]["reachable"]
            )
            unsaved = await client.post(f"{prefix}/proxy/check", json={"proxy_config": proxy})
            assert unsaved.status_code == 200 and unsaved.json()["reachable"]


@pytest.mark.asyncio
async def test_old_unsupported_profile_is_readable_and_reports_a_block_without_launch(client):
    manager = get_profile_manager()
    profile = Profile(
        name="Legacy unsupported",
        proxy=ProxyConfig(
            type="socks4",
            server="proxy.example:1080",
            username="private-user",
            password="private-password",
        ),
    )
    await manager.storage.save_profile(profile)
    response = await client.get(f"/api/v1/profiles/{profile.id}")
    assert response.status_code == 200
    checked = await client.post(f"/api/v1/profiles/{profile.id}/check-proxy")
    assert checked.status_code == 200 and not checked.json()["reachable"]
    assert "Use SOCKS5" in checked.text and "private-password" not in checked.text
    with pytest.raises(ValueError, match="SOCKS4 username/password"):
        profile.to_camoufox_launch_options()


@pytest.mark.asyncio
async def test_explicitly_clearing_socks_credentials_does_not_require_a_lost_key(
    client, monkeypatch
):
    monkeypatch.setenv("CPM_SECRET_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    try:
        entry = (
            await client.post(
                "/api/v1/proxies",
                json={
                    "name": "Recover",
                    "type": "socks5",
                    "server": "proxy.example:1080",
                    "username": "u",
                    "password": "p",
                },
            )
        ).json()["data"]
        monkeypatch.delenv("CPM_SECRET_KEY")
        get_settings.cache_clear()
        cleared = await client.patch(
            f"/api/v1/proxies/{entry['id']}",
            json={"row_version": 0, "username": None, "password": None},
        )
        assert cleared.status_code == 200 and not cleared.json()["data"]["has_password"]
    finally:
        get_settings.cache_clear()
