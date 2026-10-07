"""Release checks only accept stable versions and official GitHub assets."""

import httpx
import pytest

from camoufox_pm.core import updates


@pytest.fixture
def github(monkeypatch):
    real_client = httpx.AsyncClient
    requests = []

    def serve(payload=None, status=200, failure=None):
        def handler(request):
            requests.append(request)
            if failure:
                raise failure
            return httpx.Response(status, json=payload, request=request)

        monkeypatch.setattr(
            updates.httpx,
            "AsyncClient",
            lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
        )
        return requests

    return serve


async def test_new_stable_release_uses_official_page_and_filters_foreign_assets(github):
    official = f"{updates.RELEASES}/download/v999.0.0/camoufox-pm.zip"
    requests = github(
        {
            "tag_name": "v999.0.0",
            "html_url": "https://untrusted.invalid/fake-release",
            "assets": [
                {"name": "desktop.zip", "browser_download_url": official},
                {
                    "name": "foreign.zip",
                    "browser_download_url": "https://untrusted.invalid/app.zip",
                },
                {
                    "name": "old.zip",
                    "browser_download_url": f"{updates.RELEASES}/download/v0.1.0/app.zip",
                },
            ],
        }
    )
    result = await updates.check_update()
    assert result.available
    assert result.latest_version == "999.0.0"
    assert result.release_url == f"{updates.RELEASES}/tag/v999.0.0"
    assert [(asset.name, asset.url) for asset in result.assets] == [("desktop.zip", official)]
    assert len(requests) == 1
    assert (
        str(requests[0].url)
        == "https://api.github.com/repos/polyackiy/camoufox-profile-manager/releases/latest"
    )
    assert requests[0].headers["Accept"] == "application/vnd.github+json"


async def test_current_release_is_not_offered_as_an_update(github, monkeypatch):
    monkeypatch.setattr(updates, "__version__", "1.2.3")
    github({"tag_name": "v1.2.3", "assets": []})
    result = await updates.check_update()
    assert result.error is None
    assert not result.available


@pytest.mark.parametrize(("tag", "available"), [("v0.5.0", False), ("v0.6.0", True)])
async def test_preview_can_upgrade_to_final_without_downgrading(
    github, monkeypatch, tag, available
):
    monkeypatch.setattr(updates, "__version__", "0.6.0rc1")
    github({"tag_name": tag, "assets": []})
    result = await updates.check_update()
    assert result.error is None
    assert result.available is available


@pytest.mark.parametrize(
    "tag", ["v1.2.3-rc1", "1.2", "1.2.3/../../other", "https://bad.invalid", None]
)
async def test_invalid_tags_never_produce_an_update_link(github, tag):
    github({"tag_name": tag, "assets": []})
    result = await updates.check_update()
    assert result.error
    assert not result.available
    assert result.assets == []
    assert result.release_url == updates.RELEASES


@pytest.mark.parametrize("flag", ["draft", "prerelease"])
async def test_unpublished_or_prerelease_results_are_not_offered(github, flag):
    github({"tag_name": "v999.0.0", flag: True, "assets": []})
    result = await updates.check_update()
    assert result.error
    assert not result.available


@pytest.mark.parametrize("status", [403, 404, 429, 503])
async def test_github_http_failure_is_actionable_and_never_offers_a_download(github, status):
    github({"message": "failed"}, status=status)
    result = await updates.check_update()
    assert result.error and "try again" in result.error.lower()
    assert not result.available
    assert not result.assets


async def test_network_timeout_is_reported_without_exposing_transport_details(github):
    github(failure=httpx.ConnectTimeout("sensitive transport details"))
    result = await updates.check_update()
    assert result.error
    assert "sensitive transport details" not in result.error
    assert not result.available


@pytest.mark.parametrize("payload", [None, [], {}, {"tag_name": "v999.0.0", "assets": None}])
async def test_malformed_github_json_is_treated_as_a_failed_check(github, payload):
    github(payload)
    result = await updates.check_update()
    assert result.error
    assert not result.available
