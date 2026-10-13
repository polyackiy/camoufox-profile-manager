"""Explicit owner-approved historical ciphertext cleanup; current profiles stay intact."""

from __future__ import annotations

import json
import time
from uuid import uuid4

from camoufox_pm.core.profile_operations import run_file_operation
from camoufox_pm.sync.client import RelayError
from camoufox_pm.sync.engine import EngineError
from camoufox_pm.sync.identity import digest, encode_key
from camoufox_pm.sync.protocol import SnapshotManifest, _uuid
from camoufox_pm.sync.retention import (
    GRACE_SECONDS,
    CancellationBody,
    RetainedRevision,
    RetentionBody,
    SignedCancellation,
    SignedRetention,
    cancellation_signing_bytes,
    retention_signing_bytes,
    verify_cancellation,
    verify_retention,
)


async def _owner(runtime, user_id, password):
    engine, keys, client = await runtime._session(user_id, password)
    try:
        _, owner = runtime.service._read(user_id)
        if owner.phase != "ready" or not owner.authority:
            raise EngineError("Only the verified owner authority can manage retention")
        return engine, client, owner
    except BaseException:
        client.close()
        raise


def _candidates(runtime, state):
    result = {}
    for event in runtime.service.control.list("event/" + state.checkpoint.profile_id + "/"):
        body = json.loads(event.payload["receipt"]["body"])
        if body["operation"] == "commit":
            manifest = SnapshotManifest.model_validate(body["manifest"])
            if manifest.revision_id != state.checkpoint.revision_id:
                result[manifest.revision_id] = RetainedRevision(
                    revision_id=manifest.revision_id,
                    profile_id=manifest.profile_id,
                    manifest_sha256=digest(manifest),
                )
    return result


def _verified_status(root, decision, response):
    verify_retention(root, decision)
    if (
        type(response) is not dict
        or set(response) != {"decision", "state", "cancellation"}
        or SignedRetention.model_validate(response["decision"]) != decision
        or response["state"] not in {"scheduled", "cancelled", "collecting", "collected"}
    ):
        raise EngineError("Retention response does not match the signed owner decision")
    if response["cancellation"] is not None:
        cancellation = SignedCancellation.model_validate(response["cancellation"])
        verify_cancellation(root, cancellation, decision)
        if response["state"] != "cancelled":
            raise EngineError("Retention cancellation state is inconsistent")
    elif response["state"] == "cancelled":
        raise EngineError("Retention cancellation requires owner proof")
    return response


async def listing(runtime, user_id, local_id, password=None):
    engine, client, owner = await _owner(runtime, user_id, password)
    try:
        state = await run_file_operation(engine.reconcile, local_id)
        candidates = _candidates(runtime, state)
        decisions = []
        records = [
            item
            for item in runtime.service.control.list("retention/")
            if item.payload.get("local_id") == local_id
        ]
        deadline = time.monotonic() + 60
        for index, record in enumerate(reversed(records)):
            if record.payload.get("local_id") != local_id:
                continue
            decision = SignedRetention.model_validate(record.payload["decision"])
            verify_retention(owner.root, decision)
            try:
                if index >= 32 or time.monotonic() >= deadline:
                    raise RelayError("Retention status budget exhausted")
                response = await run_file_operation(
                    client.retention_status, decision.body.decision_id
                )
                verified = _verified_status(owner.root, decision, response)
            except RelayError:
                verified = {
                    "decision": decision.model_dump(mode="json"),
                    "state": "unconfirmed",
                    "cancellation": None,
                }
            locally_cancelled = record.payload.get("cancellation")
            if locally_cancelled is not None and verified["state"] != "cancelled":
                # An uncertain cancellation must remain visible as uncertain;
                # never let a stale relay turn it back into a confirmed schedule.
                verified = {**verified, "state": "cancellation_pending"}
            decisions.append(verified)
        await runtime._authorize(user_id)
        return {
            "revisions": [item.model_dump(mode="json") for item in candidates.values()],
            "decisions": decisions,
            "grace_seconds": GRACE_SECONDS,
        }
    finally:
        client.close()


async def schedule(runtime, user_id, local_id, revisions, *, acknowledge_cleanup, password=None):
    if (
        acknowledge_cleanup is not True
        or not isinstance(revisions, list)
        or not 1 <= len(revisions) <= 64
    ):
        raise EngineError("Select up to 64 historical revisions and confirm their cleanup")
    selected = sorted(_uuid(value) for value in revisions)
    if len(set(selected)) != len(selected):
        raise EngineError("Duplicate retention selection")
    engine, client, owner = await _owner(runtime, user_id, password)
    try:
        state = await run_file_operation(engine.reconcile, local_id)
        if state.pending or state.forked:
            raise EngineError("Resolve pending transfers before scheduling cleanup")
        candidates = _candidates(runtime, state)
        if not set(selected) <= candidates.keys():
            raise EngineError("Only verified non-current historical revisions can be selected")
        decision = None
        for existing in runtime.service.control.list("retention/"):
            if existing.payload.get("local_id") == local_id and not existing.payload.get(
                "cancellation"
            ):
                previous = SignedRetention.model_validate(existing.payload["decision"])
                if sorted(item.revision_id for item in previous.body.revisions) == selected:
                    decision = previous
                    break
        if decision is None:
            await runtime._authorize(user_id)
            authority, _ = await run_file_operation(runtime.service._keys, owner, password)
            await runtime._authorize(user_id)
            now = int(time.time())
            body = RetentionBody(
                owner_id=owner.root.owner_id,
                root_digest=digest(owner.root),
                decision_id=str(uuid4()),
                issued_at=now,
                not_before=now + GRACE_SECONDS,
                revisions=tuple(candidates[value] for value in selected),
            )
            decision = SignedRetention(
                body=body,
                signature=encode_key(authority.authority_key.sign(retention_signing_bytes(body))),
            )
            verify_retention(owner.root, decision)
            await runtime._authorize(user_id)
            runtime.service.control.create(
                "retention/" + body.decision_id,
                {
                    "local_id": local_id,
                    "decision": decision.model_dump(mode="json"),
                },
            )
        await runtime._authorize(user_id)
        result = await run_file_operation(client.retention_schedule, decision)
        return _verified_status(owner.root, decision, result)
    finally:
        client.close()


async def cancel(runtime, user_id, local_id, decision_id, password=None):
    decision_id = _uuid(decision_id)
    engine, client, owner = await _owner(runtime, user_id, password)
    try:
        await run_file_operation(engine.reconcile, local_id)
        record = runtime.service.control.get("retention/" + decision_id)
        if record is None or record.payload.get("local_id") != local_id:
            raise EngineError("Unknown owner retention decision")
        decision = SignedRetention.model_validate(record.payload["decision"])
        verify_retention(owner.root, decision)
        if record.payload.get("cancellation"):
            cancellation = SignedCancellation.model_validate(record.payload["cancellation"])
        else:
            await runtime._authorize(user_id)
            authority, _ = await run_file_operation(runtime.service._keys, owner, password)
            await runtime._authorize(user_id)
            body = CancellationBody(
                owner_id=owner.root.owner_id,
                root_digest=digest(owner.root),
                decision_id=decision_id,
                decision_digest=digest(decision),
                issued_at=int(time.time()),
            )
            cancellation = SignedCancellation(
                body=body,
                signature=encode_key(
                    authority.authority_key.sign(cancellation_signing_bytes(body))
                ),
            )
            runtime.service.control.update(
                record.key,
                {
                    **record.payload,
                    "cancellation": cancellation.model_dump(mode="json"),
                },
                record.revision,
            )
        verify_cancellation(owner.root, cancellation, decision)
        await runtime._authorize(user_id)
        response = await run_file_operation(client.retention_cancel, cancellation)
        verified = _verified_status(owner.root, decision, response)
        if (
            verified["state"] != "cancelled"
            or SignedCancellation.model_validate(verified["cancellation"]) != cancellation
        ):
            raise EngineError(
                "Cancellation remains pending until its exact signed proof is acknowledged"
            )
        return verified
    finally:
        client.close()
