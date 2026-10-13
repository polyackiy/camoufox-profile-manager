"""Bounded signed relay transport. Responses are untrusted until engine validation."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from pathlib import Path
from uuid import uuid4

import httpx

from camoufox_pm.core.update_permissions import create_private_file, reject_reparse_paths
from camoufox_pm.sync.identity import (
    AuthoritySecrets,
    DeviceSecrets,
    OwnerRoot,
    SignedMembership,
    encode_key,
)
from camoufox_pm.sync.protocol import MAX_CIPHERTEXT_BYTES, _uuid
from camoufox_pm.sync.relay import registration_signing_bytes, request_signing_bytes

MAX_RESPONSE = 512 * 1024
_CHUNK = 1024 * 1024
RPC_DEADLINE_SECONDS = 60
TRANSFER_DEADLINE_SECONDS = 1800


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise RelayError("Relay operation exceeded its time limit")


def _response_chunks(response: httpx.Response, deadline: float):
    # Do not ask iter_raw to aggregate into larger chunks: an adversarial drip
    # feed could then hide many successful socket reads inside one next() call.
    iterator = iter(response.iter_raw())
    while True:
        _check_deadline(deadline)
        try:
            part = next(iterator)
        except StopIteration:
            _check_deadline(deadline)
            return
        _check_deadline(deadline)
        yield part


class RelayError(ValueError):
    """Sanitized transport failure; the engine retains all pending local state."""


class RelayCollected(RelayError):
    """Owner-authorized old ciphertext collection, independently verified."""


class RelayConflict(RelayError):
    """Remote state changed; retry requires explicit reconciliation."""


def _json(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RelayError("Invalid relay response")
        result[key] = value
    return result


class RelayClient:
    def __init__(
        self,
        root: OwnerRoot,
        device_id: str,
        keys: DeviceSecrets,
        *,
        transport: httpx.BaseTransport | None = None,
        authorize=None,
    ):
        self.root = OwnerRoot.model_validate(root.model_dump())
        self.device_id = _uuid(device_id)
        self._keys = keys
        self.authorize = authorize
        self._http = httpx.Client(
            base_url=self.root.relay_url,
            trust_env=False,
            verify=True,
            follow_redirects=False,
            timeout=httpx.Timeout(30, connect=10),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
            transport=transport,
            headers={"Accept-Encoding": "identity"},
        )

    def __repr__(self) -> str:
        return "RelayClient(<protected credentials>)"

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _headers(self, method: str, path: str, body_hash: str) -> dict[str, str]:
        timestamp, nonce = int(time.time()), str(uuid4())
        message = request_signing_bytes(
            self.root.relay_url,
            method,
            path,
            self.root.owner_id,
            self.device_id,
            timestamp,
            nonce,
            body_hash,
        )
        return {
            "X-Sync-Device": self.device_id,
            "X-Sync-Time": str(timestamp),
            "X-Sync-Nonce": nonce,
            "X-Sync-Body-SHA256": body_hash,
            "X-Sync-Signature": encode_key(self._keys.signing_key.sign(message)),
        }

    @staticmethod
    def _accepted(response: httpx.Response) -> None:
        if response.status_code == 409:
            raise RelayConflict("Remote state changed; reconciliation is required")
        if not 200 <= response.status_code < 300:
            raise RelayError("Relay request failed")
        if response.headers.get("content-encoding", "identity") != "identity":
            raise RelayError("Unexpected relay encoding")

    def _response(
        self, response: httpx.Response, deadline: float, *, collected: bool = False
    ) -> dict:
        _check_deadline(deadline)
        if not (collected and response.status_code == 410):
            self._accepted(response)
        elif response.headers.get("content-encoding", "identity") != "identity":
            raise RelayError("Unexpected relay encoding")
        raw = bytearray()
        for part in _response_chunks(response, deadline):
            if len(raw) + len(part) > MAX_RESPONSE:
                raise RelayError("Relay response exceeds limit")
            raw.extend(part)
        result = json.loads(
            raw,
            object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        if type(result) is not dict:
            raise RelayError("Invalid relay response")
        _check_deadline(deadline)
        return result

    def register(
        self, authority: AuthoritySecrets, membership: SignedMembership, registration_token: str
    ) -> dict:
        deadline = time.monotonic() + RPC_DEADLINE_SECONDS
        try:
            payload = _json(
                {
                    "root": self.root.model_dump(mode="json"),
                    "membership": membership.model_dump(mode="json"),
                    "signature": encode_key(
                        authority.authority_key.sign(
                            registration_signing_bytes(self.root, membership)
                        )
                    ),
                }
            )
            _check_deadline(deadline)
            with self._http.stream(
                "POST",
                "/v1/register",
                content=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Registration-Token": registration_token,
                },
            ) as response:
                return self._response(response, deadline)
        except RelayConflict:
            raise
        except Exception:
            raise RelayError("Relay registration failed") from None

    def rpc(self, payload: dict) -> dict:
        deadline = time.monotonic() + RPC_DEADLINE_SECONDS
        try:
            raw = _json(payload)
            if len(raw) > MAX_RESPONSE:
                raise ValueError
            path = f"/v1/owners/{self.root.owner_id}/rpc"
            headers = self._headers("POST", path, hashlib.sha256(raw).hexdigest())
            headers["Content-Type"] = "application/json"
            _check_deadline(deadline)
            if (
                payload.get("operation")
                in {
                    "claim",
                    "commit",
                    "release",
                    "takeover",
                    "update_membership",
                    "retention_schedule",
                    "retention_cancel",
                }
                and self.authorize is not None
            ):
                self.authorize()
            with self._http.stream("POST", path, content=raw, headers=headers) as response:
                return self._response(response, deadline)
        except RelayConflict:
            raise
        except Exception:
            raise RelayError("Relay operation failed") from None

    def retention_schedule(self, decision) -> dict:
        if self.authorize is not None:
            self.authorize()
        return self.rpc(
            {
                "operation": "retention_schedule",
                "decision": decision.model_dump(mode="json")
                if hasattr(decision, "model_dump")
                else decision,
            }
        )

    def retention_cancel(self, cancellation) -> dict:
        if self.authorize is not None:
            self.authorize()
        return self.rpc(
            {
                "operation": "retention_cancel",
                "cancellation": cancellation.model_dump(mode="json")
                if hasattr(cancellation, "model_dump")
                else cancellation,
            }
        )

    def retention_status(self, decision_id: str) -> dict:
        return self.rpc({"operation": "retention_status", "decision_id": _uuid(decision_id)})

    def upload(
        self, revision_id: str, source: Path, expected_hash: str, expected_size: int
    ) -> dict:
        """Upload immutable bytes within 30 minutes, retaining source on failure."""
        deadline = time.monotonic() + TRANSFER_DEADLINE_SECONDS
        try:
            _uuid(revision_id)
            if not 41 <= expected_size <= MAX_CIPHERTEXT_BYTES:
                raise ValueError
            reject_reparse_paths(source)
            descriptor = os.open(
                source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            )
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size != expected_size:
                    raise ValueError
                checksum = hashlib.sha256()
                count = 0
                while True:
                    _check_deadline(deadline)
                    part = stream.read(_CHUNK)
                    _check_deadline(deadline)
                    if not part:
                        break
                    count += len(part)
                    if count > expected_size:
                        raise ValueError
                    checksum.update(part)
                    _check_deadline(deadline)
                if checksum.hexdigest() != expected_hash:
                    raise ValueError
                if count != expected_size:
                    raise ValueError

                def upload_rpc(payload):
                    _check_deadline(deadline)
                    if self.authorize is not None:
                        self.authorize()
                    raw = _json(payload)
                    path = f"/v1/owners/{self.root.owner_id}/rpc"
                    headers = self._headers("POST", path, hashlib.sha256(raw).hexdigest())
                    headers["Content-Type"] = "application/json"
                    with self._http.stream("POST", path, content=raw, headers=headers) as response:
                        return self._response(response, deadline)

                status = upload_rpc(
                    {
                        "operation": "upload_begin",
                        "revision_id": revision_id,
                        "ciphertext_length": expected_size,
                        "ciphertext_sha256": expected_hash,
                    }
                )
                if (
                    set(status) != {"revision_id", "offset", "chunk_bytes"}
                    or status["revision_id"] != revision_id
                    or type(status["offset"]) is not int
                    or not 0 <= status["offset"] <= expected_size
                    or status["chunk_bytes"] != 8 * 1024 * 1024
                ):
                    raise ValueError
                offset = status["offset"]
                stream.seek(offset)
                while offset < expected_size:
                    _check_deadline(deadline)
                    part = stream.read(min(8 * 1024 * 1024, expected_size - offset))
                    if not part:
                        raise ValueError
                    if self.authorize is not None:
                        self.authorize()
                    path = f"/v1/owners/{self.root.owner_id}/uploads/{revision_id}/chunks/{offset}"
                    headers = self._headers("PUT", path, hashlib.sha256(part).hexdigest())
                    headers.update(
                        {
                            "Content-Length": str(len(part)),
                            "Content-Type": "application/octet-stream",
                        }
                    )
                    with self._http.stream("PUT", path, content=part, headers=headers) as response:
                        result = self._response(response, deadline)
                    offset += len(part)
                    if result != {"revision_id": revision_id, "offset": offset}:
                        raise ValueError
                if stream.read(1):
                    raise ValueError
                result = upload_rpc({"operation": "upload_finish", "revision_id": revision_id})
                if result != {
                    "revision_id": revision_id,
                    "ciphertext_sha256": expected_hash,
                    "ciphertext_length": expected_size,
                }:
                    raise ValueError
                return result
        except RelayConflict:
            raise
        except Exception:
            raise RelayError("Encrypted upload failed; local copy retained") from None

    def download(
        self, revision_id: str, destination: Path, expected_hash: str, expected_size: int
    ) -> None:
        """Verify bytes within 30 minutes; never overwrite an existing copy.

        Socket inactivity timeouts bound a stalled read to at most 30 seconds
        beyond the overall deadline. Partial files are removed on timeout.
        """
        deadline = time.monotonic() + TRANSFER_DEADLINE_SECONDS
        created = False
        try:
            _uuid(revision_id)
            if not 41 <= expected_size <= MAX_CIPHERTEXT_BYTES:
                raise ValueError
            path = f"/v1/owners/{self.root.owner_id}/blobs/{revision_id}"
            headers = self._headers("GET", path, hashlib.sha256(b"").hexdigest())
            _check_deadline(deadline)
            with self._http.stream("GET", path, headers=headers) as response:
                _check_deadline(deadline)
                if response.status_code == 410:
                    from .retention import verify_collected

                    evidence = self._response(response, deadline, collected=True)
                    verify_collected(self.root, evidence, revision_id, expected_hash, expected_size)
                    raise RelayCollected(
                        "Ciphertext removed by an owner-authorized retention decision"
                    )
                self._accepted(response)
                if response.headers.get("content-length") != str(expected_size):
                    raise ValueError
                fd = create_private_file(destination)
                created = True
                with os.fdopen(fd, "wb") as output:
                    checksum, count = hashlib.sha256(), 0
                    for part in _response_chunks(response, deadline):
                        count += len(part)
                        if count > expected_size:
                            raise ValueError
                        output.write(part)
                        checksum.update(part)
                    if count != expected_size or checksum.hexdigest() != expected_hash:
                        raise ValueError
                    output.flush()
                    os.fsync(output.fileno())
                    _check_deadline(deadline)
        except RelayCollected:
            raise
        except Exception:
            if created:
                try:
                    destination.unlink()
                except OSError:
                    pass
            raise RelayError("Encrypted download failed") from None
