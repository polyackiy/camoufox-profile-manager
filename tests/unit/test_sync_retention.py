"""Owner-authorized delayed collection preserves heads and signed history."""

import json
import os
import sqlite3
import time
from uuid import uuid4

import httpx
import psutil
import pytest

from camoufox_pm.sync.client import RelayClient, RelayCollected, RelayError
from camoufox_pm.sync.identity import digest, encode_key
from camoufox_pm.sync.relay import _Store
from camoufox_pm.sync.relay_retention import collect_retained_ciphertext
from camoufox_pm.sync.retention import (
    GRACE_SECONDS,
    CancellationBody,
    RetainedRevision,
    RetentionBody,
    SignedCancellation,
    SignedRetention,
    cancellation_signing_bytes,
    retention_signing_bytes,
    verify_collected,
)
from tests.unit.test_sync_relay import Relay


@pytest.fixture
def retained(tmp_path):
    relay = Relay(tmp_path.resolve() / "relay")
    relay.claim()
    manifests = []
    for _ in range(2):
        manifest = relay.manifest(b"x" * 100)
        assert relay.upload(manifest.revision_id, b"x" * 100).status_code == 200
        assert relay.commit(manifest).status_code == 200
        manifests.append(manifest)
    return relay, manifests


def decision_for(relay, manifest, *, issued=None):
    now = int(time.time()) if issued is None else issued
    body = RetentionBody(
        owner_id=relay.root.owner_id,
        root_digest=digest(relay.root),
        decision_id=str(uuid4()),
        issued_at=now,
        not_before=now + GRACE_SECONDS,
        revisions=(
            RetainedRevision(
                revision_id=manifest.revision_id,
                profile_id=manifest.profile_id,
                manifest_sha256=digest(manifest),
            ),
        ),
    )
    return SignedRetention(
        body=body,
        signature=encode_key(relay.secrets.authority_key.sign(retention_signing_bytes(body))),
    )


def schedule(relay, decision):
    return relay.rpc("retention_schedule", decision=decision.model_dump(mode="json"))


def cancellation_for(relay, decision):
    body = CancellationBody(
        owner_id=relay.root.owner_id,
        root_digest=digest(relay.root),
        decision_id=decision.body.decision_id,
        decision_digest=digest(decision),
        issued_at=int(time.time()),
    )
    return SignedCancellation(
        body=body,
        signature=encode_key(relay.secrets.authority_key.sign(cancellation_signing_bytes(body))),
    )


def advance(monkeypatch):
    future = int(time.time()) + GRACE_SECONDS + 100
    monkeypatch.setattr(time, "time", lambda: future)


def test_delay_collection_preserves_head_metadata_and_verifiable_evidence(retained, monkeypatch):
    relay, (old, head) = retained
    decision = decision_for(relay, old)
    assert schedule(relay, decision).json()["state"] == "scheduled"
    assert collect_retained_ciphertext(relay.directory)["examined"] == 0
    advance(monkeypatch)
    assert collect_retained_ciphertext(relay.directory)["collected"] == 1
    assert collect_retained_ciphertext(relay.directory)["examined"] == 0
    path = f"/v1/owners/{relay.root.owner_id}/blobs/{old.revision_id}"
    result = relay.client.get(path, headers=relay.headers("GET", path))
    assert result.status_code == 410
    assert (
        verify_collected(
            relay.root, result.json(), old.revision_id, old.ciphertext_sha256, old.ciphertext_length
        )
        == decision
    )
    assert relay.rpc("manifest", revision_id=old.revision_id).json()["manifest"] == old.model_dump(
        mode="json"
    )
    assert relay.checkpoint()["revision_id"] == head.revision_id
    assert (
        len(relay.rpc("history", profile_id=relay.profile, after_sequence=0).json()["events"]) == 3
    )
    assert (relay.directory / "blobs" / f"{relay.root.owner_id}-{head.revision_id}.blob").exists()
    assert (
        relay.rpc("retention_status", decision_id=decision.body.decision_id).json()["state"]
        == "collected"
    )


def test_root_proof_current_head_and_binding_rejections(retained):
    relay, (old, head) = retained
    assert schedule(relay, decision_for(relay, head)).status_code == 409
    decision = decision_for(relay, old)
    tampered = decision.model_dump(mode="json")
    tampered["body"]["revisions"][0]["manifest_sha256"] = "0" * 64
    assert relay.rpc("retention_schedule", decision=tampered).status_code == 400
    decision = decision.model_copy(
        update={"signature": encode_key(relay.key.sign(retention_signing_bytes(decision.body)))}
    )
    assert schedule(relay, decision).status_code == 400


def test_retention_retry_has_full_reception_grace_and_cancellation_is_durable(
    retained, monkeypatch
):
    relay, (old, _) = retained
    decision = decision_for(relay, old, issued=int(time.time()) - 10000)
    assert schedule(relay, decision).status_code == 200
    with sqlite3.connect(relay.directory / "relay.sqlite3") as db:
        eligible = db.execute("SELECT eligible_at FROM retention_decisions").fetchone()[0]
        assert eligible >= int(time.time()) + GRACE_SECONDS
    cancellation = cancellation_for(relay, decision)
    for _ in range(2):
        assert (
            relay.rpc("retention_cancel", cancellation=cancellation.model_dump(mode="json")).json()[
                "state"
            ]
            == "cancelled"
        )
    assert schedule(relay, decision).json()["state"] == "cancelled"
    advance(monkeypatch)
    assert collect_retained_ciphertext(relay.directory)["examined"] == 0
    assert (relay.directory / "blobs" / f"{relay.root.owner_id}-{old.revision_id}.blob").exists()


def test_live_download_leases_block_collection_until_worker_exits(retained, monkeypatch):
    relay, (old, _) = retained
    schedule(relay, decision_for(relay, old))
    with sqlite3.connect(relay.directory / "relay.sqlite3") as db:
        db.execute(
            "INSERT INTO active_downloads VALUES(?,?,?,?,?)",
            (
                str(uuid4()),
                relay.root.owner_id,
                old.revision_id,
                os.getpid(),
                psutil.Process().create_time(),
            ),
        )
    advance(monkeypatch)
    assert collect_retained_ciphertext(relay.directory)["skipped"] == 1
    with sqlite3.connect(relay.directory / "relay.sqlite3") as db:
        db.execute("UPDATE active_downloads SET created=0")
    assert collect_retained_ciphertext(relay.directory)["collected"] == 1


def test_collecting_fence_survives_crash_and_clock_rollback(retained, monkeypatch):
    relay, (old, _) = retained
    decision = decision_for(relay, old)
    schedule(relay, decision)
    advance(monkeypatch)
    original = _Store.sync_directory

    def fail(path):
        if path.name == "blobs":
            raise OSError("crash before quota release")
        original(path)

    monkeypatch.setattr(_Store, "sync_directory", staticmethod(fail))
    assert collect_retained_ciphertext(relay.directory)["failed"] == 1
    cancellation = cancellation_for(relay, decision)
    assert (
        relay.rpc("retention_cancel", cancellation=cancellation.model_dump(mode="json")).status_code
        == 409
    )
    with sqlite3.connect(relay.directory / "relay.sqlite3") as db:
        assert db.execute(
            "SELECT state,size FROM revisions WHERE id=?", (old.revision_id,)
        ).fetchone() == ("collecting", 100)
    monkeypatch.setattr(_Store, "sync_directory", staticmethod(original))
    earlier = int(time.time()) - 100
    monkeypatch.setattr(time, "time", lambda: earlier)
    assert collect_retained_ciphertext(relay.directory)["collected"] == 1
    with sqlite3.connect(relay.directory / "relay.sqlite3") as db:
        assert db.execute(
            "SELECT state,size FROM revisions WHERE id=?", (old.revision_id,)
        ).fetchone() == ("collected", 0)


def test_client_verifies_collected_proof_before_distinct_error(retained, monkeypatch, tmp_path):
    relay, (old, _) = retained
    schedule(relay, decision_for(relay, old))
    advance(monkeypatch)
    collect_retained_ciphertext(relay.directory)
    corrupt = False

    def transport(request):
        result = relay.client.request(
            request.method, request.url.path, headers=dict(request.headers), content=request.read()
        )
        data = result.content
        if corrupt:
            evidence = result.json()
            evidence["retention"]["body"]["revisions"][0]["manifest_sha256"] = "0" * 64
            data = json.dumps(evidence).encode()
        return httpx.Response(
            result.status_code, stream=httpx.ByteStream(data), headers=result.headers
        )

    with RelayClient(
        relay.root, relay.device_id, relay.secrets.device, transport=httpx.MockTransport(transport)
    ) as client:
        with pytest.raises(RelayCollected):
            client.download(
                old.revision_id, tmp_path / "missing", old.ciphertext_sha256, old.ciphertext_length
            )
        corrupt = True
        with pytest.raises(RelayError) as error:
            client.download(
                old.revision_id, tmp_path / "missing", old.ciphertext_sha256, old.ciphertext_length
            )
        assert not isinstance(error.value, RelayCollected)
    assert not (tmp_path / "missing").exists()


def test_cancellation_and_collection_race_has_one_winner(retained, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    relay, (old, _) = retained
    decision = decision_for(relay, old)
    schedule(relay, decision)
    advance(monkeypatch)
    cancellation = cancellation_for(relay, decision)
    with ThreadPoolExecutor(max_workers=2) as pool:
        cancel = pool.submit(
            relay.rpc, "retention_cancel", cancellation=cancellation.model_dump(mode="json")
        )
        collect = pool.submit(collect_retained_ciphertext, relay.directory)
        response, result = cancel.result(), collect.result()
    if response.status_code == 200:
        assert response.json()["state"] == "cancelled" and result["collected"] == 0
    else:
        assert response.status_code == 409 and result["collected"] == 1


def test_download_lease_removed_after_response(retained):
    relay, (_, head) = retained
    path = f"/v1/owners/{relay.root.owner_id}/blobs/{head.revision_id}"
    assert relay.client.get(path, headers=relay.headers("GET", path)).status_code == 200
    with sqlite3.connect(relay.directory / "relay.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM active_downloads").fetchone()[0] == 0


def test_recheck_current_head_after_scheduling_blocks_gc(retained, monkeypatch):
    relay, (old, _) = retained
    schedule(relay, decision_for(relay, old))
    with sqlite3.connect(relay.directory / "relay.sqlite3") as db:
        db.execute("UPDATE profiles SET head=? WHERE id=?", (old.revision_id, relay.profile))
    advance(monkeypatch)
    assert collect_retained_ciphertext(relay.directory)["skipped"] == 1
    assert (relay.directory / "blobs" / f"{relay.root.owner_id}-{old.revision_id}.blob").exists()


@pytest.mark.parametrize("live", [False, True])
def test_reader_quota_reaps_dead_current_head_leases_independent_of_gc(retained, live):
    relay, (_, head) = retained
    with sqlite3.connect(relay.directory / "relay.sqlite3") as db:
        db.executemany(
            "INSERT INTO active_downloads VALUES(?,?,?,?,?)",
            [
                (
                    str(uuid4()),
                    relay.root.owner_id,
                    head.revision_id,
                    os.getpid(),
                    psutil.Process().create_time() if live else 0,
                )
                for _ in range(4096)
            ],
        )
    path = f"/v1/owners/{relay.root.owner_id}/blobs/{head.revision_id}"
    result = relay.client.get(path, headers=relay.headers("GET", path))
    assert result.status_code == (429 if live else 200)
    with sqlite3.connect(relay.directory / "relay.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM active_downloads").fetchone()[0] == (
            4096 if live else 0
        )
