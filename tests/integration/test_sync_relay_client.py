"""Actual client signatures cross the standalone relay's ASGI boundary."""

from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from camoufox_pm.sync.client import RelayClient, RelayConflict
from camoufox_pm.sync.identity import bootstrap
from camoufox_pm.sync.relay import create_relay_app


def test_client_registration_claim_release_and_reopen(tmp_path):
    origin = "https://relay.example"
    token = "test-registration-secret-" + "x" * 32
    root, keys, membership = bootstrap(origin)
    device_id = membership.membership.devices[0].device_id
    profile_id = str(uuid4())
    app = create_relay_app(
        tmp_path / "relay", relay_url=origin, registration_token=SecretStr(token)
    )
    with TestClient(app, base_url=origin) as server:

        def forward(request):
            answer = server.request(
                request.method,
                str(request.url),
                headers=dict(request.headers),
                content=request.read(),
            )
            return httpx.Response(
                answer.status_code, headers=answer.headers, stream=httpx.ByteStream(answer.content)
            )

        with RelayClient(
            root, device_id, keys.device, transport=httpx.MockTransport(forward)
        ) as client:
            assert client.register(keys.authority, membership, token)["owner_id"] == root.owner_id
            current = client.rpc({"operation": "membership"})
            assert current["membership"] == membership.model_dump(mode="json")
            empty = client.rpc({"operation": "checkpoint", "profile_id": profile_id})["checkpoint"]
            held = client.rpc({"operation": "claim", "profile_id": profile_id, "expected": empty})[
                "checkpoint"
            ]
            assert held["holder_device_id"] == device_id
            with pytest.raises(RelayConflict):
                client.rpc({"operation": "claim", "profile_id": profile_id, "expected": empty})
            released = client.rpc(
                {"operation": "release", "profile_id": profile_id, "expected": held}
            )["checkpoint"]
            assert released["holder_device_id"] is None
            second = client.rpc(
                {"operation": "claim", "profile_id": profile_id, "expected": released}
            )["checkpoint"]
            assert second["checkout_generation"] == 2
            assert (
                len(
                    client.rpc(
                        {"operation": "history", "profile_id": profile_id, "after_sequence": 0}
                    )["events"]
                )
                == 3
            )
