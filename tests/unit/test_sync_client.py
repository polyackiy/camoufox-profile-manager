"""Transport failures must never turn into trusted state or destroy local data."""

import hashlib
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from camoufox_pm.sync.client import MAX_RESPONSE, RelayClient, RelayConflict, RelayError
from camoufox_pm.sync.identity import bootstrap, decode_key
from camoufox_pm.sync.relay import request_signing_bytes


@pytest.fixture
def identity():
    return bootstrap("https://relay.example")


def client(identity, handler):
    root, keys, membership = identity
    return RelayClient(
        root,
        membership.membership.devices[0].device_id,
        keys.device,
        transport=httpx.MockTransport(handler),
    )


def response(raw=b"{}", status=200, headers=None):
    return httpx.Response(status, stream=httpx.ByteStream(raw), headers=headers)


def test_signed_exact_body_and_origin(identity):
    root, _, membership = identity

    def handler(request):
        raw = request.read()
        assert request.url == f"https://relay.example/v1/owners/{root.owner_id}/rpc"
        assert hashlib.sha256(raw).hexdigest() == request.headers["X-Sync-Body-SHA256"]
        message = request_signing_bytes(
            root.relay_url,
            request.method,
            request.url.path,
            root.owner_id,
            request.headers["X-Sync-Device"],
            int(request.headers["X-Sync-Time"]),
            request.headers["X-Sync-Nonce"],
            hashlib.sha256(raw).hexdigest(),
        )
        Ed25519PublicKey.from_public_bytes(
            decode_key(membership.membership.devices[0].signing_public)
        ).verify(decode_key(request.headers["X-Sync-Signature"], 64), message)
        return response(b'{"ok":true}')

    with client(identity, handler) as relay:
        assert relay.rpc({"operation": "membership"}) == {"ok": True}


@pytest.mark.parametrize(
    "raw",
    [b"[]", b'{"a":1,"a":2}', b'{"a":NaN}', b"x", b" " * (MAX_RESPONSE + 1)],
    ids=["array", "duplicate-key", "nonfinite", "invalid-json", "oversized"],
)
def test_bad_json_is_bounded_and_sanitized(identity, raw):
    with client(identity, lambda _: response(raw)) as relay, pytest.raises(RelayError) as error:
        relay.rpc({"operation": "membership"})
    assert str(error.value) == "Relay operation failed"


@pytest.mark.parametrize("status", [301, 302, 307, 308, 401, 403, 500])
def test_no_redirect_or_error_body_trust(identity, status):
    calls = []

    def handler(request):
        calls.append(request)
        return response(b"SECRET", status, {"Location": "https://attacker.example"})

    with client(identity, handler) as relay, pytest.raises(RelayError):
        relay.rpc({"operation": "membership"})
    assert len(calls) == 1


def test_conflict_is_distinct(identity):
    with client(identity, lambda _: response(status=409)) as relay, pytest.raises(RelayConflict):
        relay.rpc({"operation": "claim"})


def test_download_private_and_no_overwrite(identity, tmp_path):
    data = b"x" * 80
    target = tmp_path / "ciphertext"
    with client(identity, lambda _: response(data, headers={"Content-Length": "80"})) as relay:
        relay.download(str(uuid4()), target, hashlib.sha256(data).hexdigest(), 80)
        assert target.read_bytes() == data
        with pytest.raises(RelayError):
            relay.download(str(uuid4()), target, hashlib.sha256(data).hexdigest(), 80)
        assert target.read_bytes() == data


@pytest.mark.parametrize(
    "raw,headers",
    [
        (b"x" * 79, {"Content-Length": "80"}),
        (b"x" * 81, {"Content-Length": "80"}),
        (b"y" * 80, {"Content-Length": "80"}),
        (b"x" * 80, {}),
        (b"x" * 80, {"Content-Length": "80", "Content-Encoding": "gzip"}),
    ],
)
def test_bad_download_removed(identity, tmp_path, raw, headers):
    target = tmp_path / "ciphertext"
    with (
        client(identity, lambda _: response(raw, headers=headers)) as relay,
        pytest.raises(RelayError),
    ):
        relay.download(str(uuid4()), target, hashlib.sha256(b"x" * 80).hexdigest(), 80)
    assert not target.exists()


def test_upload_validates_before_network(identity, tmp_path):
    target = tmp_path / "ciphertext"
    target.write_bytes(b"x" * 80)

    def unexpected(_):
        pytest.fail("invalid upload must not reach network")

    with client(identity, unexpected) as relay, pytest.raises(RelayError):
        relay.upload(str(uuid4()), target, "0" * 64, 80)
    assert target.read_bytes() == b"x" * 80


def test_upload_keeps_source_on_failure(identity, tmp_path):
    target = tmp_path / "ciphertext"
    data = b"x" * 80
    target.write_bytes(data)

    def handler(request):
        assert request.read() == data
        assert request.headers["Content-Length"] == "80"
        return response(status=503)

    with client(identity, handler) as relay, pytest.raises(RelayError):
        relay.upload(str(uuid4()), target, hashlib.sha256(data).hexdigest(), 80)
    assert target.read_bytes() == data


def test_registration_token_not_in_error(identity):
    _, keys, membership = identity
    with (
        client(identity, lambda _: response(status=403)) as relay,
        pytest.raises(RelayError) as error,
    ):
        relay.register(keys.authority, membership, "registration-private")
    assert "registration-private" not in str(error.value)


class TimedStream(httpx.SyncByteStream):
    def __init__(self, clock, parts, delay, *, final_delay=0, failure=None):
        self.clock = clock
        self.parts = parts
        self.delay = delay
        self.final_delay = final_delay
        self.failure = failure
        self.closed = False

    def __iter__(self):
        for part in self.parts:
            self.clock[0] += self.delay
            yield part
        self.clock[0] += self.final_delay
        if self.failure:
            raise self.failure

    def close(self):
        self.closed = True


@pytest.fixture
def clock(monkeypatch):
    from types import SimpleNamespace

    from camoufox_pm.sync import client as transport

    clock = [0.0]
    real_time = transport.time.time
    monkeypatch.setattr(
        transport, "time", SimpleNamespace(monotonic=lambda: clock[0], time=real_time)
    )
    return clock


@pytest.mark.parametrize("operation", ["rpc", "register"])
def test_overall_rpc_deadline_rejects_drip_feed(identity, clock, operation):
    stream = TimedStream(clock, [b" ", b" ", b"{", b"}"], 20)
    with client(identity, lambda _: httpx.Response(200, stream=stream)) as relay:
        with pytest.raises(RelayError):
            if operation == "rpc":
                relay.rpc({"operation": "membership"})
            else:
                _, keys, membership = identity
                relay.register(keys.authority, membership, "secret-token")
    assert clock[0] == 60
    assert stream.closed


@pytest.mark.parametrize("operation", ["upload", "download"])
def test_overall_transfer_deadline_cleans_download_retains_upload(
    identity, tmp_path, clock, operation
):
    source = tmp_path / "ciphertext"
    data = b"x" * 80
    if operation == "upload":
        source.write_bytes(data)
    stream = TimedStream(
        clock, [data[:20]] * 4 if operation == "download" else [b" ", b" ", b"{", b"}"], 600
    )
    with client(
        identity, lambda _: httpx.Response(200, stream=stream, headers={"Content-Length": "80"})
    ) as relay:
        with pytest.raises(RelayError):
            getattr(relay, operation)(str(uuid4()), source, hashlib.sha256(data).hexdigest(), 80)
    assert clock[0] == 1800
    assert stream.closed
    if operation == "upload":
        assert source.read_bytes() == data
    else:
        assert not source.exists()


def test_rpc_deadline_also_checks_end_of_stream(identity, clock):
    stream = TimedStream(clock, [b"{}"], 1, final_delay=60)
    with client(identity, lambda _: httpx.Response(200, stream=stream)) as relay:
        with pytest.raises(RelayError):
            relay.rpc({"operation": "membership"})
    assert stream.closed


def test_upload_source_hashing_counts_against_deadline(identity, tmp_path, clock, monkeypatch):
    from camoufox_pm.sync import client as transport

    target = tmp_path / "ciphertext"
    data = b"x" * 80
    target.write_bytes(data)
    original = transport.os.fdopen

    class SlowReader:
        def __init__(self, file):
            self.file = file

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.file.close()

        def fileno(self):
            return self.file.fileno()

        def read(self, count):
            result = self.file.read(count)
            clock[0] += 1800
            return result

    monkeypatch.setattr(transport.os, "fdopen", lambda *a, **kw: SlowReader(original(*a, **kw)))

    def forbidden(_):
        pytest.fail("source hashing used the entire budget before any network request")

    with client(identity, forbidden) as relay, pytest.raises(RelayError):
        relay.upload(str(uuid4()), target, hashlib.sha256(data).hexdigest(), 80)
    assert target.read_bytes() == data


@pytest.mark.parametrize(
    "failure", [httpx.ReadTimeout("PRIVATE-CREDENTIAL"), OSError("PRIVATE-CREDENTIAL")]
)
def test_download_read_failure_sanitized_and_partial_removed(identity, tmp_path, clock, failure):
    stream = TimedStream(clock, [b"x" * 20], 1, failure=failure)
    destination = tmp_path / "partial"
    with client(
        identity, lambda _: httpx.Response(200, stream=stream, headers={"Content-Length": "80"})
    ) as relay:
        with pytest.raises(RelayError) as error:
            relay.download(str(uuid4()), destination, hashlib.sha256(b"x" * 80).hexdigest(), 80)
    assert "PRIVATE-CREDENTIAL" not in str(error.value)
    assert stream.closed
    assert not destination.exists()


def test_upload_rechecks_authorization_after_hashing_before_transport(
    identity, tmp_path, monkeypatch
):
    import camoufox_pm.sync.client as module

    source = tmp_path / "encrypted"
    source.write_bytes(b"ciphertext" * 10)
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    original = module.hashlib.sha256
    hashed = False

    class ObservedHash:
        def __init__(self):
            self.inner = original()

        def update(self, value):
            self.inner.update(value)

        def hexdigest(self):
            nonlocal hashed
            hashed = True
            return self.inner.hexdigest()

    monkeypatch.setattr(module.hashlib, "sha256", ObservedHash)

    def authorize():
        assert hashed
        raise PermissionError("account was demoted while hashing")

    instance = client(
        identity, lambda request: pytest.fail("revoked upload must not reach transport")
    )
    instance.authorize = authorize
    with pytest.raises(RelayError):
        instance.upload(str(uuid4()), source, checksum, source.stat().st_size)
    assert source.read_bytes() == b"ciphertext" * 10
    instance.close()


@pytest.mark.parametrize(
    "operation", ["claim", "commit", "release", "takeover", "update_membership"]
)
def test_mutating_rpc_rechecks_authorization_before_transport(identity, operation):
    instance = client(identity, lambda request: pytest.fail("revoked request reached transport"))

    def authorize():
        raise PermissionError("revoked")

    instance.authorize = authorize
    with pytest.raises(RelayError):
        instance.rpc({"operation": operation})
    instance.close()
