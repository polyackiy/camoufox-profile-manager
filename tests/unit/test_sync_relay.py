"""Real relay ASGI, device signatures, durable checkout and ciphertext integration."""

import hashlib
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from camoufox_pm.sync.identity import (
    approve_membership,
    bootstrap,
    encode_key,
    generate_device,
)
from camoufox_pm.sync.protocol import SUITE, SnapshotManifest, signing_bytes
from camoufox_pm.sync.relay import (
    create_relay_app,
    registration_signing_bytes,
    request_signing_bytes,
)

ORIGIN = "http://127.0.0.1"
TOKEN = "operator-token-generated-outside-relay-123456789"


class Relay:
    def __init__(self, directory, **limits):
        self.directory = directory
        self.app = create_relay_app(
            directory,
            relay_url=ORIGIN,
            registration_token=SecretStr(TOKEN),
            development_loopback=True,
            **limits,
        )
        self.client = TestClient(self.app, base_url=ORIGIN, client=("127.0.0.1", 50000))
        self.root, self.secrets, self.membership = bootstrap(ORIGIN, allow_loopback_http=True)
        self.device_id = self.membership.membership.devices[0].device_id
        self.key = self.secrets.device.signing_key
        self.profile = str(uuid4())
        self.register_body = {
            "root": self.root.model_dump(mode="json"),
            "membership": self.membership.model_dump(mode="json"),
            "signature": encode_key(
                self.secrets.authority_key.sign(
                    registration_signing_bytes(self.root, self.membership)
                )
            ),
        }
        assert (
            self.client.post(
                "/v1/register", json=self.register_body, headers={"X-Registration-Token": TOKEN}
            ).status_code
            == 200
        )

    def headers(
        self, method, path, content=b"", *, key=None, device=None, nonce=None, timestamp=None
    ):
        timestamp = int(time.time()) if timestamp is None else timestamp
        nonce = nonce or str(uuid4())
        device = device or self.device_id
        checksum = hashlib.sha256(content).hexdigest()
        signature = (key or self.key).sign(
            request_signing_bytes(
                ORIGIN, method, path, self.root.owner_id, device, timestamp, nonce, checksum
            )
        )
        return {
            "X-Sync-Device": device,
            "X-Sync-Time": str(timestamp),
            "X-Sync-Nonce": nonce,
            "X-Sync-Body-SHA256": checksum,
            "X-Sync-Signature": encode_key(signature),
        }

    def rpc(self, operation, **args):
        path = f"/v1/owners/{self.root.owner_id}/rpc"
        raw = json.dumps(dict(operation=operation, **args), separators=(",", ":")).encode()
        return self.client.post(path, content=raw, headers=self.headers("POST", path, raw))

    def checkpoint(self):
        response = self.rpc("checkpoint", profile_id=self.profile)
        assert response.status_code == 200, response.text
        return response.json()["checkpoint"]

    def claim(self):
        response = self.rpc("claim", profile_id=self.profile, expected=self.checkpoint())
        assert response.status_code == 200, response.text
        return response.json()["checkpoint"]

    def upload(self, revision, content):
        path = f"/v1/owners/{self.root.owner_id}/blobs/{revision}"
        return self.client.put(path, content=content, headers=self.headers("PUT", path, content))

    def manifest(self, ciphertext, *, revision=None, expected=None):
        checkpoint = expected or self.checkpoint()
        identities = sorted(
            [("device", d.device_id) for d in self.membership.membership.devices]
            + [("recovery", self.root.recovery_id)]
        )
        return SnapshotManifest(
            version=1,
            suite=SUITE,
            owner_id=self.root.owner_id,
            profile_id=self.profile,
            revision_id=revision or str(uuid4()),
            parent_revision_id=checkpoint["revision_id"],
            writer_device_id=self.device_id,
            membership_epoch=checkpoint["membership_epoch"],
            checkout_generation=checkpoint["checkout_generation"],
            ciphertext_sha256=hashlib.sha256(ciphertext).hexdigest(),
            ciphertext_length=len(ciphertext),
            recipients=tuple(
                {
                    "kind": kind,
                    "recipient_id": rid,
                    "enc": encode_key(bytes(32)),
                    "wrapped_key": encode_key(bytes(48)),
                }
                for kind, rid in identities
            ),
        )

    def commit(self, manifest, *, expected=None, signature=None):
        return self.rpc(
            "commit",
            profile_id=self.profile,
            expected=expected or self.checkpoint(),
            manifest=manifest.model_dump(mode="json"),
            signature=signature or encode_key(self.key.sign(signing_bytes(manifest))),
        )


@pytest.fixture
def relay(tmp_path):
    instance = Relay(tmp_path.resolve() / "relay")
    yield instance
    instance.client.close()


def test_registration_operator_auth_root_pinning(relay):
    assert relay.client.post("/v1/register", json=relay.register_body).status_code == 403
    assert (
        relay.client.post(
            "/v1/register", json=relay.register_body, headers={"X-Registration-Token": TOKEN}
        ).status_code
        == 409
    )
    corrupted = dict(relay.register_body, signature=encode_key(bytes(64)))
    assert (
        relay.client.post(
            "/v1/register", json=corrupted, headers={"X-Registration-Token": TOKEN}
        ).status_code
        == 400
    )


def test_signed_request_replay_body_and_clock_binding(relay):
    path = f"/v1/owners/{relay.root.owner_id}/rpc"
    raw = b'{"operation":"membership"}'
    headers = relay.headers("POST", path, raw)
    assert relay.client.post(path, content=raw, headers=headers).status_code == 200
    assert relay.client.post(path, content=raw, headers=headers).status_code == 409
    assert relay.client.post(path, content=raw + b" ", headers=headers).status_code == 409
    fresh = relay.headers("POST", path, raw)
    assert relay.client.post(path, content=raw + b" ", headers=fresh).status_code == 403
    stale = relay.headers("POST", path, raw, timestamp=int(time.time()) - 301)
    assert relay.client.post(path, content=raw, headers=stale).status_code == 403
    unknown = relay.headers("POST", path, raw, device=str(uuid4()))
    assert relay.client.post(path, content=raw, headers=unknown).status_code == 403


def test_request_cannot_cross_owner_or_endpoint(relay):
    path = f"/v1/owners/{relay.root.owner_id}/rpc"
    raw = b'{"operation":"membership"}'
    headers = relay.headers("POST", path, raw)
    other_path = f"/v1/owners/{uuid4()}/rpc"
    assert relay.client.post(other_path, content=raw, headers=headers).status_code == 403
    assert (
        relay.client.post(path + "?ignored=true", content=raw, headers=headers).status_code == 400
    )


def test_checkout_durable_no_timeout_steal_and_exact_cas(relay):
    original = relay.checkpoint()
    held = relay.claim()
    assert held["checkout_generation"] == 1 and held["holder_device_id"] == relay.device_id
    assert relay.rpc("claim", profile_id=relay.profile, expected=held).status_code == 409
    assert relay.rpc("release", profile_id=relay.profile, expected=original).status_code == 409
    bool_epoch = dict(held, membership_epoch=True)
    assert relay.rpc("release", profile_id=relay.profile, expected=bool_epoch).status_code == 409
    released = relay.rpc("release", profile_id=relay.profile, expected=held)
    assert released.status_code == 200
    assert released.json()["checkpoint"]["holder_device_id"] is None
    assert relay.claim()["checkout_generation"] == 2
    reopened = create_relay_app(
        relay.directory,
        relay_url=ORIGIN,
        registration_token=SecretStr(TOKEN),
        development_loopback=True,
    )
    client = TestClient(reopened, base_url=ORIGIN, client=("127.0.0.1", 50001))
    path = f"/v1/owners/{relay.root.owner_id}/rpc"
    raw = json.dumps({"operation": "checkpoint", "profile_id": relay.profile}).encode()
    result = client.post(path, content=raw, headers=relay.headers("POST", path, raw))
    assert result.json()["checkpoint"]["checkout_generation"] == 2
    client.close()


def test_concurrent_claim_only_one_wins(relay):
    initial = relay.checkpoint()

    def claim(_):
        return relay.rpc("claim", profile_id=relay.profile, expected=initial).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(claim, [1, 2])) == [200, 409]


def test_upload_commit_download_history(relay):
    held = relay.claim()
    ciphertext = b"opaque ciphertext" * 4
    manifest = relay.manifest(ciphertext)
    assert relay.upload(manifest.revision_id, ciphertext).status_code == 200
    assert relay.upload(manifest.revision_id, ciphertext).status_code == 409
    path = f"/v1/owners/{relay.root.owner_id}/blobs/{manifest.revision_id}"
    assert relay.client.get(path, headers=relay.headers("GET", path)).status_code == 404
    response = relay.commit(manifest, expected=held)
    assert response.status_code == 200, response.text
    assert response.json()["checkpoint"]["revision_id"] == manifest.revision_id
    assert relay.commit(manifest, expected=held).status_code == 409
    downloaded = relay.client.get(path, headers=relay.headers("GET", path))
    assert downloaded.content == ciphertext
    assert downloaded.headers["cache-control"] == "no-store"
    assert relay.rpc("manifest", revision_id=manifest.revision_id).json()[
        "manifest"
    ] == manifest.model_dump(mode="json")
    history = relay.rpc("history", profile_id=relay.profile, after_sequence=0).json()
    assert len(history["events"]) == 2
    assert json.loads(history["events"][1]["receipt"]["body"])["operation"] == "commit"
    assert relay.rpc("discard_upload", revision_id=manifest.revision_id).status_code == 409


def test_commit_rejects_ciphertext_mismatch_signature_and_recipients(relay):
    relay.claim()
    ciphertext = b"x" * 50
    manifest = relay.manifest(ciphertext)
    assert relay.upload(manifest.revision_id, ciphertext).status_code == 200
    assert relay.commit(manifest, signature=encode_key(bytes(64))).status_code == 400
    changed = manifest.model_copy(update={"ciphertext_sha256": "0" * 64})
    assert relay.commit(changed).status_code == 409
    changed = manifest.model_copy(update={"membership_epoch": 2})
    assert relay.commit(changed).status_code == 409
    assert relay.checkpoint()["revision_id"] is None


def test_membership_revocation_immediately_blocks_old_device(relay):
    replacement = generate_device()
    replacement_id = str(uuid4())
    successor = approve_membership(
        relay.root,
        relay.secrets.authority_key,
        (replacement.certificate(relay.root.owner_id, replacement_id),),
        previous=relay.membership,
    )
    assert (
        relay.rpc("update_membership", membership=successor.model_dump(mode="json")).status_code
        == 200
    )
    assert relay.rpc("membership").status_code == 403
    relay.device_id, relay.key, relay.membership = (
        replacement_id,
        replacement.signing_key,
        successor,
    )
    assert relay.rpc("membership").status_code == 200
    assert (
        relay.rpc("update_membership", membership=successor.model_dump(mode="json")).status_code
        == 400
    )
    assert len(relay.rpc("memberships", after_epoch=0).json()["memberships"]) == 2


def test_upload_digest_and_length_mismatch_no_publish(relay):
    revision = str(uuid4())
    path = f"/v1/owners/{relay.root.owner_id}/blobs/{revision}"
    headers = relay.headers("PUT", path, b"x" * 50)
    response = relay.client.put(path, content=b"y" * 50, headers=headers)
    assert response.status_code == 400
    assert not list((relay.directory / "blobs").iterdir())
    assert relay.upload(revision, b"x" * 50).status_code == 409  # aborted ID stays consumed


def test_quotas_discard_and_strict_resource_ids(tmp_path):
    relay = Relay(tmp_path.resolve() / "relay", max_blob_bytes=100, owner_quota_bytes=100)
    revision = str(uuid4())
    assert relay.upload(revision, b"x" * 60).status_code == 200
    assert relay.upload(str(uuid4()), b"x" * 60).status_code == 429
    assert relay.upload(str(uuid4()), b"x" * 101).status_code == 413
    assert relay.rpc("discard_upload", revision_id=revision).status_code == 200
    assert relay.upload(str(uuid4()), b"x" * 60).status_code == 200
    assert relay.rpc("checkpoint", profile_id="../../escape").status_code == 400
    relay.client.close()


def test_https_required_and_development_not_exposed(relay, tmp_path):
    remote = TestClient(relay.app, base_url=ORIGIN, client=("192.0.2.10", 50000))
    assert remote.post("/v1/register").status_code == 400
    remote.close()
    app = create_relay_app(
        tmp_path.resolve() / "production",
        relay_url="https://relay.example",
        registration_token=SecretStr(TOKEN),
    )
    client = TestClient(app, base_url="http://relay.example")
    assert client.post("/v1/register").status_code == 400
    client.close()


def test_strict_json_duplicate_keys_and_unknown_operations(relay):
    path = f"/v1/owners/{relay.root.owner_id}/rpc"
    raw = b'{"operation":"membership","operation":"membership"}'
    assert (
        relay.client.post(path, content=raw, headers=relay.headers("POST", path, raw)).status_code
        == 400
    )
    assert relay.rpc("steal_checkout").status_code == 400
    assert relay.rpc("membership", unknown=True).status_code == 400


def test_restart_rejects_unknown_database_schema(relay):
    with sqlite3.connect(relay.directory / "relay.sqlite3") as db:
        db.execute("PRAGMA user_version=999")
    with pytest.raises(ValueError):
        create_relay_app(
            relay.directory,
            relay_url=ORIGIN,
            registration_token=SecretStr(TOKEN),
            development_loopback=True,
        )


def test_real_ciphertext_roundtrip(relay, tmp_path):
    pytest.importorskip("cryptography.hazmat.primitives.hpke")
    pytest.importorskip("nacl.bindings")
    from camoufox_pm.core.update_permissions import create_private_directory
    from camoufox_pm.sync.crypto import decrypt_file, encrypt_file
    from camoufox_pm.sync.protocol import SnapshotContext

    held = relay.claim()
    directory = tmp_path.resolve() / "crypto"
    create_private_directory(directory)
    source = directory / "source"
    source.write_bytes(b"browser cookies encrypted before transport")
    template = relay.manifest(b"x" * 45)
    manifest, signature = encrypt_file(
        source,
        directory / "cipher",
        template,
        {
            ("device", relay.device_id): relay.secrets.device.encryption_key.public_key(),
            ("recovery", relay.root.recovery_id): relay.secrets.recovery_key.public_key(),
        },
        relay.key,
    )
    ciphertext = (directory / "cipher").read_bytes()
    assert relay.upload(manifest.revision_id, ciphertext).status_code == 200
    assert relay.commit(manifest, expected=held, signature=encode_key(signature)).status_code == 200
    path = f"/v1/owners/{relay.root.owner_id}/blobs/{manifest.revision_id}"
    downloaded = relay.client.get(path, headers=relay.headers("GET", path))
    (directory / "download").write_bytes(downloaded.content)
    (directory / "download").chmod(0o600)
    expected = SnapshotContext(
        **{
            name: getattr(manifest, name)
            for name in (
                "owner_id",
                "profile_id",
                "revision_id",
                "parent_revision_id",
                "writer_device_id",
                "membership_epoch",
                "checkout_generation",
            )
        },
        recipient_identities=tuple((r.kind, r.recipient_id) for r in manifest.recipients),
    )
    decrypt_file(
        directory / "download",
        directory / "restored",
        manifest,
        signature,
        expected,
        "device",
        relay.device_id,
        relay.secrets.device.encryption_key,
        relay.key.public_key(),
    )
    assert (directory / "restored").read_bytes() == source.read_bytes()


def test_explicit_authority_takeover_requires_revocation_and_warning(relay):
    from camoufox_pm.sync.relay import takeover_signing_bytes

    held = relay.claim()
    operation_id = str(uuid4())
    approval = encode_key(
        relay.secrets.authority_key.sign(
            takeover_signing_bytes(
                relay.root.owner_id, relay.profile, held, operation_id, relay.device_id
            )
        )
    )
    assert (
        relay.rpc(
            "takeover",
            profile_id=relay.profile,
            expected=held,
            operation_id=operation_id,
            warning_ack=True,
            authority_signature=approval,
        ).status_code
        == 409
    )
    replacement = generate_device()
    replacement_id = str(uuid4())
    successor = approve_membership(
        relay.root,
        relay.secrets.authority_key,
        (replacement.certificate(relay.root.owner_id, replacement_id),),
        previous=relay.membership,
    )
    assert (
        relay.rpc("update_membership", membership=successor.model_dump(mode="json")).status_code
        == 200
    )
    relay.device_id, relay.key, relay.membership = (
        replacement_id,
        replacement.signing_key,
        successor,
    )
    expected = relay.checkpoint()
    approval = encode_key(
        relay.secrets.authority_key.sign(
            takeover_signing_bytes(
                relay.root.owner_id, relay.profile, expected, operation_id, replacement_id
            )
        )
    )
    arguments = {
        "profile_id": relay.profile,
        "expected": expected,
        "operation_id": operation_id,
        "warning_ack": True,
        "authority_signature": approval,
    }
    assert relay.rpc("takeover", **dict(arguments, warning_ack=False)).status_code == 409
    assert (
        relay.rpc(
            "takeover", **dict(arguments, authority_signature=encode_key(bytes(64)))
        ).status_code
        == 400
    )
    response = relay.rpc("takeover", **arguments)
    assert response.status_code == 200, response.text
    assert response.json()["checkpoint"]["checkout_generation"] == 2
    assert response.json()["checkpoint"]["holder_device_id"] == replacement_id
    assert relay.rpc("takeover", **arguments).status_code == 409


def test_signed_transition_receipt_verifies_independently(relay):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    from camoufox_pm.sync.identity import decode_key

    relay.claim()
    receipt = relay.rpc("history", profile_id=relay.profile, after_sequence=0).json()["events"][0][
        "receipt"
    ]
    assert hashlib.sha256(receipt["body"].encode()).hexdigest() == receipt["body_sha256"]
    message = request_signing_bytes(
        **{
            key: receipt[key]
            for key in (
                "relay_url",
                "method",
                "path",
                "owner_id",
                "device_id",
                "timestamp",
                "nonce",
                "body_sha256",
            )
        }
    )
    Ed25519PublicKey.from_public_bytes(
        decode_key(relay.membership.membership.devices[0].signing_public)
    ).verify(decode_key(receipt["signature"], 64), message)


def test_clock_rollback_fails_closed_after_nonce_pruning(relay, monkeypatch):
    from camoufox_pm.sync import relay as module

    now = int(time.time())
    monkeypatch.setattr(module.time, "time", lambda: now + 1000)
    assert relay.rpc("membership").status_code == 200
    monkeypatch.setattr(module.time, "time", lambda: now)
    assert relay.rpc("membership").status_code == 503


def test_commit_rejects_missing_ciphertext_even_with_ready_metadata(relay):
    relay.claim()
    ciphertext = b"x" * 50
    manifest = relay.manifest(ciphertext)
    assert relay.upload(manifest.revision_id, ciphertext).status_code == 200
    blob = relay.directory / "blobs" / (relay.root.owner_id + "-" + manifest.revision_id + ".blob")
    blob.unlink()
    assert relay.commit(manifest).status_code == 400
    assert relay.checkpoint()["revision_id"] is None


def test_duplicate_authentication_headers_rejected(relay):
    path = f"/v1/owners/{relay.root.owner_id}/rpc"
    raw = b'{"operation":"membership"}'
    headers = list(relay.headers("POST", path, raw).items())
    headers.append(("X-Sync-Device", relay.device_id))
    assert relay.client.post(path, content=raw, headers=headers).status_code == 403


def test_blob_status_reconciles_lost_upload_and_commit_responses(relay):
    relay.claim()
    ciphertext = b"x" * 50
    manifest = relay.manifest(ciphertext)
    assert relay.rpc("blob_status", revision_id=manifest.revision_id).status_code == 404
    assert relay.upload(manifest.revision_id, ciphertext).status_code == 200
    status = relay.rpc("blob_status", revision_id=manifest.revision_id)
    assert status.json() == {
        "revision_id": manifest.revision_id,
        "writer_device_id": relay.device_id,
        "ciphertext_sha256": manifest.ciphertext_sha256,
        "ciphertext_length": 50,
        "state": "ready",
    }
    assert relay.commit(manifest).status_code == 200
    assert relay.rpc("blob_status", revision_id=manifest.revision_id).json()["state"] == "committed"
    replacement = generate_device()
    replacement_id = str(uuid4())
    successor = approve_membership(
        relay.root,
        relay.secrets.authority_key,
        (replacement.certificate(relay.root.owner_id, replacement_id),),
        previous=relay.membership,
    )
    assert (
        relay.rpc("update_membership", membership=successor.model_dump(mode="json")).status_code
        == 200
    )
    relay.device_id, relay.key = replacement_id, replacement.signing_key
    assert relay.rpc("blob_status", revision_id=manifest.revision_id).status_code == 403


def test_crashed_pending_upload_discard_removes_bytes_before_quota_release(relay):
    import os

    from camoufox_pm.core.update_permissions import create_private_file

    revision = str(uuid4())
    partial = relay.directory / "blobs" / (".pending-" + relay.root.owner_id + "-" + revision)
    with os.fdopen(create_private_file(partial), "wb") as stream:
        stream.write(b"partial crashed ciphertext")
    with sqlite3.connect(relay.directory / "relay.sqlite3") as db:
        db.execute(
            "INSERT INTO revisions VALUES(?,?,?,?,?,'uploading',NULL,NULL)",
            (relay.root.owner_id, revision, relay.device_id, 100, "0" * 64),
        )
    assert relay.rpc("blob_status", revision_id=revision).json()["ciphertext_length"] == 100
    assert relay.rpc("discard_upload", revision_id=revision).status_code == 200
    assert not partial.exists()
    status = relay.rpc("blob_status", revision_id=revision).json()
    assert status["state"] == "aborted" and status["ciphertext_length"] == 0


def test_pending_delete_failure_retains_quota_reservation(relay, monkeypatch):
    from pathlib import Path

    revision = str(uuid4())
    with sqlite3.connect(relay.directory / "relay.sqlite3") as db:
        db.execute(
            "INSERT INTO revisions VALUES(?,?,?,?,?,'uploading',NULL,NULL)",
            (relay.root.owner_id, revision, relay.device_id, 100, "0" * 64),
        )
    original = Path.unlink

    def fail_pending(self, *args, **kwargs):
        if self.name.startswith(".pending-"):
            raise PermissionError("busy")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_pending)
    assert relay.rpc("discard_upload", revision_id=revision).status_code == 400
    status = relay.rpc("blob_status", revision_id=revision).json()
    assert status["state"] == "aborting" and status["ciphertext_length"] == 100


@pytest.mark.asyncio
async def test_download_slots_released_on_deadline(tmp_path):
    import asyncio

    from camoufox_pm.sync.relay import _LimitedFileResponse

    path = tmp_path / "blob"
    path.write_bytes(b"x" * 80)
    slots = asyncio.Semaphore(1)
    response = _LimitedFileResponse(path, slots=slots, deadline=0.01)
    scope = {"type": "http", "method": "GET", "headers": [], "extensions": {}}

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        await asyncio.Event().wait()

    await response(scope, receive, send)
    assert not slots.locked()


@pytest.mark.asyncio
async def test_download_slots_reject_excess_request(tmp_path):
    import asyncio

    from camoufox_pm.sync.relay import _LimitedFileResponse

    path = tmp_path / "blob"
    path.write_bytes(b"x" * 80)
    slots = asyncio.Semaphore(0)
    response = _LimitedFileResponse(path, slots=slots)
    sent = []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)

    await response({"type": "http", "method": "GET", "headers": []}, receive, send)
    assert sent[0]["status"] == 429
    assert slots.locked()


def test_active_upload_cannot_release_quota_before_worker_exits(relay):
    import psutil

    revision = str(uuid4())
    process = psutil.Process()
    with sqlite3.connect(relay.directory / "relay.sqlite3") as db:
        db.execute(
            "INSERT INTO revisions VALUES(?,?,?,?,?,'uploading',NULL,NULL)",
            (relay.root.owner_id, revision, relay.device_id, 100, "0" * 64),
        )
        db.execute(
            "INSERT INTO active_uploads VALUES(?,?,?,?)",
            (relay.root.owner_id, revision, process.pid, process.create_time()),
        )
    assert relay.rpc("discard_upload", revision_id=revision).status_code == 409
    assert relay.rpc("blob_status", revision_id=revision).json()["ciphertext_length"] == 100


def test_unsigned_rpc_does_not_buffer_body(relay, monkeypatch):
    from starlette.requests import Request

    read = []
    original = Request.stream

    async def observed(self):
        read.append(True)
        async for part in original(self):
            yield part

    monkeypatch.setattr(Request, "stream", observed)
    result = relay.client.post(f"/v1/owners/{relay.root.owner_id}/rpc", content=b"x" * 1000)
    assert result.status_code >= 400
    assert not read


def test_registration_existing_owner_conflict_precedes_capacity_limit(tmp_path):
    relay = Relay(tmp_path.resolve() / "relay", max_owners=1)
    try:
        response = relay.client.post(
            "/v1/register", json=relay.register_body, headers={"X-Registration-Token": TOKEN}
        )
        assert response.status_code == 409
    finally:
        relay.client.close()


@pytest.mark.asyncio
async def test_old_certificate_request_cannot_cross_remove_and_readd_rotation(tmp_path):
    import asyncio

    import httpx

    relay = Relay(tmp_path.resolve() / "relay")
    peer = generate_device()
    peer_id = str(uuid4())
    peer_certificate = peer.certificate(relay.root.owner_id, peer_id)
    membership2 = approve_membership(
        relay.root,
        relay.secrets.authority_key,
        relay.membership.membership.devices + (peer_certificate,),
        previous=relay.membership,
    )
    assert (
        relay.rpc("update_membership", membership=membership2.model_dump(mode="json")).status_code
        == 200
    )
    body_started, release_body = asyncio.Event(), asyncio.Event()
    raw = b'{"operation":"membership"}'
    path = f"/v1/owners/{relay.root.owner_id}/rpc"

    async def delayed_body():
        body_started.set()
        await release_body.wait()
        yield raw

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=relay.app, client=("127.0.0.1", 50001)), base_url=ORIGIN
    ) as client:
        pending = asyncio.create_task(
            client.post(path, content=delayed_body(), headers=relay.headers("POST", path, raw))
        )
        await asyncio.wait_for(body_started.wait(), 3)
        try:
            membership3 = approve_membership(
                relay.root, relay.secrets.authority_key, (peer_certificate,), previous=membership2
            )
            replacement = generate_device().certificate(relay.root.owner_id, relay.device_id)
            membership4 = approve_membership(
                relay.root,
                relay.secrets.authority_key,
                (peer_certificate, replacement),
                previous=membership3,
            )
            for membership in (membership3, membership4):
                body = json.dumps(
                    {
                        "operation": "update_membership",
                        "membership": membership.model_dump(mode="json"),
                    }
                ).encode()
                result = await client.post(
                    path,
                    content=body,
                    headers=relay.headers("POST", path, body, key=peer.signing_key, device=peer_id),
                )
                assert result.status_code == 200
        finally:
            release_body.set()
        result = await pending
        assert result.status_code == 403
    relay.client.close()


def overdue(relay, revision):
    with sqlite3.connect(relay.directory / "relay.sqlite3") as db:
        db.execute(
            "UPDATE upload_expiry SET expires=? WHERE revision=?", (int(time.time()) - 10, revision)
        )


def test_expiry_grace_terminal_tombstone_and_bound(relay):
    from camoufox_pm.sync.relay import expire_uncommitted_uploads

    revisions = [str(uuid4()) for _ in range(2)]
    for revision in revisions:
        assert relay.upload(revision, b"x" * 100).status_code == 200
    assert expire_uncommitted_uploads(relay.directory)["examined"] == 0
    for revision in revisions:
        overdue(relay, revision)
    assert expire_uncommitted_uploads(relay.directory, limit=1)["expired"] == 1
    assert expire_uncommitted_uploads(relay.directory)["expired"] == 1
    assert expire_uncommitted_uploads(relay.directory)["examined"] == 0
    for revision in revisions:
        status = relay.rpc("blob_status", revision_id=revision).json()
        assert status["state"] == "expired" and status["ciphertext_length"] == 0
        assert relay.upload(revision, b"x" * 100).status_code == 409
        assert not (relay.directory / "blobs" / f"{relay.root.owner_id}-{revision}.blob").exists()


@pytest.mark.parametrize("live", [True, False])
def test_expiry_worker_liveness_and_crashed_partial(relay, live):
    import os

    import psutil

    from camoufox_pm.core.update_permissions import create_private_file
    from camoufox_pm.sync.relay import expire_uncommitted_uploads

    revision = str(uuid4())
    assert relay.upload(revision, b"x" * 100).status_code == 200
    overdue(relay, revision)
    partial = relay.directory / "blobs" / f".pending-{relay.root.owner_id}-{revision}"
    with os.fdopen(create_private_file(partial), "wb") as stream:
        stream.write(b"partial")
    with sqlite3.connect(relay.directory / "relay.sqlite3") as db:
        db.execute("UPDATE revisions SET state='uploading' WHERE id=?", (revision,))
        db.execute(
            "INSERT INTO active_uploads VALUES(?,?,?,?)",
            (
                relay.root.owner_id,
                revision,
                os.getpid(),
                psutil.Process().create_time() if live else 0,
            ),
        )
    result = expire_uncommitted_uploads(relay.directory)
    assert result["skipped" if live else "expired"] == 1
    assert partial.exists() == live


@pytest.mark.parametrize("failure", ["unlink", "fsync"])
def test_expiry_failure_retains_quota(relay, monkeypatch, failure):
    from pathlib import Path

    from camoufox_pm.sync.relay import _Store, expire_uncommitted_uploads

    revision = str(uuid4())
    assert relay.upload(revision, b"x" * 100).status_code == 200
    overdue(relay, revision)
    if failure == "unlink":
        original = Path.unlink

        def deny(path, *args, **kwargs):
            if path.name.endswith(".blob"):
                raise PermissionError("private path")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", deny)
    else:
        original_sync = _Store.sync_directory

        def deny_sync(path):
            if path.name == "blobs":
                raise OSError("private path")
            original_sync(path)

        monkeypatch.setattr(_Store, "sync_directory", staticmethod(deny_sync))
    result = expire_uncommitted_uploads(relay.directory)
    assert result["failed"] == 1
    status = relay.rpc("blob_status", revision_id=revision).json()
    assert status["state"] == "expiring" and status["ciphertext_length"] == 100
    monkeypatch.undo()
    assert expire_uncommitted_uploads(relay.directory)["expired"] == 1


def test_expiry_serializes_with_commit_and_preserves_history(relay):
    from camoufox_pm.sync.relay import expire_uncommitted_uploads

    held = relay.claim()
    content = b"x" * 100
    manifest = relay.manifest(content)
    assert relay.upload(manifest.revision_id, content).status_code == 200
    overdue(relay, manifest.revision_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        commit = pool.submit(relay.commit, manifest, expected=held)
        sweep = pool.submit(expire_uncommitted_uploads, relay.directory)
        response, result = commit.result(), sweep.result()
    status = relay.rpc("blob_status", revision_id=manifest.revision_id).json()["state"]
    if response.status_code == 200:
        assert status == "committed" and result["expired"] == 0
        assert expire_uncommitted_uploads(relay.directory)["expired"] == 0
    else:
        assert response.status_code == 409 and status == "expired" and result["expired"] == 1


def test_expiry_clock_rollback_fails_closed(relay):
    from camoufox_pm.sync.relay import expire_uncommitted_uploads

    revision = str(uuid4())
    assert relay.upload(revision, b"x" * 100).status_code == 200
    overdue(relay, revision)
    with sqlite3.connect(relay.directory / "relay.sqlite3") as db:
        db.execute("UPDATE clock SET highwater=?", (int(time.time()) + 100,))
    with pytest.raises(ValueError, match="maintenance failed"):
        expire_uncommitted_uploads(relay.directory)
    with sqlite3.connect(relay.directory / "relay.sqlite3") as db:
        assert db.execute(
            "SELECT state,size FROM revisions WHERE id=?", (revision,)
        ).fetchone() == ("ready", 100)


def test_expiry_v1_migration_grants_full_grace(relay):
    from camoufox_pm.sync.relay import UNCOMMITTED_TTL_SECONDS, _Store, expire_uncommitted_uploads

    revision = str(uuid4())
    assert relay.upload(revision, b"x" * 100).status_code == 200
    with sqlite3.connect(relay.directory / "relay.sqlite3") as db:
        db.execute("DROP TABLE active_downloads")
        db.execute("DROP TABLE retention_items")
        db.execute("DROP TABLE retention_decisions")
        db.execute("DROP TABLE upload_offsets")
        db.execute("DROP TABLE upload_expiry")
        db.execute("PRAGMA user_version=1")
    before = int(time.time())
    _Store(relay.directory)
    with sqlite3.connect(relay.directory / "relay.sqlite3") as db:
        assert (
            db.execute(
                "SELECT expires FROM upload_expiry WHERE revision=?", (revision,)
            ).fetchone()[0]
            >= before + UNCOMMITTED_TTL_SECONDS
        )
    assert expire_uncommitted_uploads(relay.directory)["expired"] == 0


def test_expiry_crash_fence_prevents_commit_after_removed_file(relay, monkeypatch):
    from camoufox_pm.sync.relay import _Store, expire_uncommitted_uploads

    held = relay.claim()
    manifest = relay.manifest(b"x" * 100)
    assert relay.upload(manifest.revision_id, b"x" * 100).status_code == 200
    overdue(relay, manifest.revision_id)
    original = _Store.sync_directory

    class Crash(BaseException):
        pass

    def crash(path):
        if path.name == "blobs":
            raise Crash()
        original(path)

    monkeypatch.setattr(_Store, "sync_directory", staticmethod(crash))
    with pytest.raises(Crash):
        expire_uncommitted_uploads(relay.directory)
    assert relay.commit(manifest, expected=held).status_code == 409
    assert relay.rpc("discard_upload", revision_id=manifest.revision_id).status_code == 409
    status = relay.rpc("blob_status", revision_id=manifest.revision_id).json()
    assert status["state"] == "expiring" and status["ciphertext_length"] == 100
    monkeypatch.undo()
    # Reopen after crash with a backwards clock: finish the already-durable
    # deletion decision without authorizing expiry of any additional upload.
    with sqlite3.connect(relay.directory / "relay.sqlite3") as db:
        db.execute("UPDATE clock SET highwater=?", (int(time.time()) + 100,))
    assert expire_uncommitted_uploads(relay.directory)["expired"] == 1
    with sqlite3.connect(relay.directory / "relay.sqlite3") as db:
        assert db.execute(
            "SELECT state,size FROM revisions WHERE id=?", (manifest.revision_id,)
        ).fetchone() == ("expired", 0)
    assert not (
        relay.directory / "blobs" / f"{relay.root.owner_id}-{manifest.revision_id}.blob"
    ).exists()


def test_expiry_cannot_remove_committed_older_ancestor(relay):
    from camoufox_pm.sync.relay import expire_uncommitted_uploads

    relay.claim()
    for _ in range(2):
        manifest = relay.manifest(b"x" * 100)
        assert relay.upload(manifest.revision_id, b"x" * 100).status_code == 200
        assert relay.commit(manifest).status_code == 200
        overdue(relay, manifest.revision_id)
    assert expire_uncommitted_uploads(relay.directory)["examined"] == 0
    assert len(list((relay.directory / "blobs").glob("*.blob"))) == 2


def test_discard_crash_fence_blocks_commit_and_operator_finishes(relay, monkeypatch):
    from camoufox_pm.sync.relay import _Store, expire_uncommitted_uploads

    held = relay.claim()
    manifest = relay.manifest(b"x" * 100)
    assert relay.upload(manifest.revision_id, b"x" * 100).status_code == 200
    original = _Store.sync_directory

    def fail(path):
        if path.name == "blobs":
            raise OSError("simulated crash before final metadata commit")
        original(path)

    monkeypatch.setattr(_Store, "sync_directory", staticmethod(fail))
    assert relay.rpc("discard_upload", revision_id=manifest.revision_id).status_code == 400
    assert relay.commit(manifest, expected=held).status_code == 409
    status = relay.rpc("blob_status", revision_id=manifest.revision_id).json()
    assert status["state"] == "aborting" and status["ciphertext_length"] == 100
    assert not (
        relay.directory / "blobs" / f"{relay.root.owner_id}-{manifest.revision_id}.blob"
    ).exists()
    monkeypatch.undo()
    with sqlite3.connect(relay.directory / "relay.sqlite3") as db:
        db.execute("UPDATE clock SET highwater=?", (int(time.time()) + 100,))
    assert expire_uncommitted_uploads(relay.directory)["expired"] == 1
    with sqlite3.connect(relay.directory / "relay.sqlite3") as db:
        assert db.execute(
            "SELECT state,size FROM revisions WHERE id=?", (manifest.revision_id,)
        ).fetchone() == ("aborted", 0)


def start_resumable(relay, revision, data):
    return relay.rpc(
        "upload_begin",
        revision_id=revision,
        ciphertext_length=len(data),
        ciphertext_sha256=hashlib.sha256(data).hexdigest(),
    )


def send_chunk(relay, revision, offset, data, headers=None):
    path = f"/v1/owners/{relay.root.owner_id}/uploads/{revision}/chunks/{offset}"
    return relay.client.put(path, content=data, headers=headers or relay.headers("PUT", path, data))


def test_resumable_exact_prefix_duplicate_and_complete(relay):
    revision, data = str(uuid4()), b"0123456789" * 10
    assert start_resumable(relay, revision, data).json()["offset"] == 0
    assert send_chunk(relay, revision, 0, data[:40]).json()["offset"] == 40
    assert start_resumable(relay, revision, data).json()["offset"] == 40
    assert send_chunk(relay, revision, 0, data[:40]).json()["offset"] == 40
    assert send_chunk(relay, revision, 0, b"x" * 40).status_code == 409
    assert send_chunk(relay, revision, 39, data[39:]).status_code == 409
    assert send_chunk(relay, revision, 41, data[41:]).status_code == 409
    assert relay.rpc("upload_finish", revision_id=revision).status_code == 409
    assert send_chunk(relay, revision, 40, data[40:]).status_code == 200
    assert relay.rpc("upload_finish", revision_id=revision).status_code == 200
    assert relay.rpc("upload_finish", revision_id=revision).status_code == 200
    assert (
        relay.directory / "blobs" / f"{relay.root.owner_id}-{revision}.blob"
    ).read_bytes() == data


def test_resumable_rejects_identity_changes_and_bad_final_hash(relay):
    revision, data = str(uuid4()), b"x" * 100
    assert start_resumable(relay, revision, data).status_code == 200
    assert start_resumable(relay, revision, data + b"x").status_code == 409
    assert start_resumable(relay, revision, b"y" * 100).status_code == 409
    assert send_chunk(relay, revision, 0, b"y" * 100).status_code == 200
    assert relay.rpc("upload_finish", revision_id=revision).status_code == 409
    assert relay.rpc("blob_status", revision_id=revision).json()["state"] == "uploading"


def test_resumable_offset_and_chunk_hash_are_authenticated(relay):
    revision, data = str(uuid4()), b"x" * 100
    start_resumable(relay, revision, data)
    path = f"/v1/owners/{relay.root.owner_id}/uploads/{revision}/chunks/0"
    assert (
        send_chunk(relay, revision, 1, data[:20], relay.headers("PUT", path, data[:20])).status_code
        == 400
    )
    assert (
        send_chunk(relay, revision, 0, b"y" * 20, relay.headers("PUT", path, data[:20])).status_code
        == 400
    )
    assert start_resumable(relay, revision, data).json()["offset"] == 0


def test_resumable_crash_tail_truncated_before_retry(relay, monkeypatch):
    from camoufox_pm.sync.relay import _Store

    revision, data = str(uuid4()), b"x" * 100
    start_resumable(relay, revision, data)
    assert send_chunk(relay, revision, 0, data[:40]).status_code == 200
    original = _Store.sync_directory

    def crash(path):
        if path.name == "blobs":
            raise OSError("crash after append before DB commit")
        original(path)

    monkeypatch.setattr(_Store, "sync_directory", staticmethod(crash))
    assert send_chunk(relay, revision, 40, data[40:]).status_code == 400
    pending = relay.directory / "blobs" / f".pending-{relay.root.owner_id}-{revision}"
    assert pending.stat().st_size == 100
    monkeypatch.undo()
    assert start_resumable(relay, revision, data).json()["offset"] == 40
    assert send_chunk(relay, revision, 40, data[40:]).status_code == 200
    assert relay.rpc("upload_finish", revision_id=revision).status_code == 200


def test_resumable_finalize_rename_crash_reopens_safely(relay, monkeypatch):
    from camoufox_pm.sync.relay import _Store

    revision, data = str(uuid4()), b"x" * 100
    start_resumable(relay, revision, data)
    send_chunk(relay, revision, 0, data)
    original = _Store.sync_directory

    def crash(path):
        if path.name == "blobs":
            raise OSError("crash after rename before ready metadata")
        original(path)

    monkeypatch.setattr(_Store, "sync_directory", staticmethod(crash))
    assert relay.rpc("upload_finish", revision_id=revision).status_code == 400
    monkeypatch.undo()
    _Store(relay.directory)
    assert relay.rpc("blob_status", revision_id=revision).json()["state"] == "uploading"
    assert relay.rpc("upload_finish", revision_id=revision).status_code == 200


def test_resumable_expiry_rejects_late_chunk(relay):
    from camoufox_pm.sync.relay import expire_uncommitted_uploads

    revision, data = str(uuid4()), b"x" * 100
    start_resumable(relay, revision, data)
    send_chunk(relay, revision, 0, data[:40])
    overdue(relay, revision)
    assert expire_uncommitted_uploads(relay.directory)["expired"] == 1
    assert send_chunk(relay, revision, 40, data[40:]).status_code == 409
    assert start_resumable(relay, revision, data).status_code == 409


def test_real_client_resumes_existing_ciphertext_prefix(relay, tmp_path):
    import httpx

    from camoufox_pm.sync.client import RelayClient

    revision, data = str(uuid4()), b"x" * 100
    source = tmp_path / "ciphertext"
    source.write_bytes(data)
    start_resumable(relay, revision, data)
    send_chunk(relay, revision, 0, data[:40])
    sent = []

    def transport(request):
        if request.method == "PUT":
            sent.append((request.url.path, len(request.read())))
        response = relay.client.request(
            request.method, request.url.path, headers=dict(request.headers), content=request.read()
        )
        return httpx.Response(
            response.status_code,
            stream=httpx.ByteStream(response.content),
            headers=response.headers,
        )

    with RelayClient(
        relay.root, relay.device_id, relay.secrets.device, transport=httpx.MockTransport(transport)
    ) as client:
        result = client.upload(revision, source, hashlib.sha256(data).hexdigest(), len(data))
    assert result["ciphertext_length"] == 100
    assert len(sent) == 1 and sent[0][0].endswith("/chunks/40") and sent[0][1] == 60


def test_real_client_lost_chunk_ack_resumes_without_resending(relay, tmp_path):
    import httpx

    from camoufox_pm.sync.client import RelayClient, RelayError

    revision, data = str(uuid4()), b"x" * 100
    source = tmp_path / "ciphertext"
    source.write_bytes(data)
    chunks = []
    lose = True

    def transport(request):
        nonlocal lose
        response = relay.client.request(
            request.method, request.url.path, headers=dict(request.headers), content=request.read()
        )
        if request.method == "PUT":
            chunks.append(request.url.path)
            if lose:
                lose = False
                raise httpx.ReadError("ack lost")
        return httpx.Response(
            response.status_code,
            stream=httpx.ByteStream(response.content),
            headers=response.headers,
        )

    with RelayClient(
        relay.root, relay.device_id, relay.secrets.device, transport=httpx.MockTransport(transport)
    ) as client:
        with pytest.raises(RelayError):
            client.upload(revision, source, hashlib.sha256(data).hexdigest(), len(data))
        assert (
            client.upload(revision, source, hashlib.sha256(data).hexdigest(), len(data))[
                "ciphertext_length"
            ]
            == 100
        )
    assert len(chunks) == 1
    assert source.read_bytes() == data


@pytest.mark.parametrize("live", [False, True])
def test_resumable_adopts_only_dead_legacy_writer(relay, live):
    import os

    import psutil

    from camoufox_pm.core.update_permissions import create_private_file

    revision, data = str(uuid4()), b"x" * 100
    pending = relay.directory / "blobs" / f".pending-{relay.root.owner_id}-{revision}"
    with os.fdopen(create_private_file(pending), "wb") as output:
        output.write(data[:20])
    with sqlite3.connect(relay.directory / "relay.sqlite3") as db:
        db.execute(
            "INSERT INTO revisions VALUES(?,?,?,?,?,'uploading',NULL,NULL)",
            (relay.root.owner_id, revision, relay.device_id, 100, hashlib.sha256(data).hexdigest()),
        )
        db.execute(
            "INSERT INTO upload_expiry VALUES(?,?,?)",
            (relay.root.owner_id, revision, int(time.time()) + 1000),
        )
        db.execute(
            "INSERT INTO active_uploads VALUES(?,?,?,?)",
            (
                relay.root.owner_id,
                revision,
                os.getpid(),
                psutil.Process().create_time() if live else 0,
            ),
        )
    result = start_resumable(relay, revision, data)
    if live:
        assert result.status_code == 409 and pending.read_bytes() == data[:20]
    else:
        assert result.json()["offset"] == 0 and not pending.exists()
        assert send_chunk(relay, revision, 0, data).status_code == 200
        assert relay.rpc("upload_finish", revision_id=revision).status_code == 200
