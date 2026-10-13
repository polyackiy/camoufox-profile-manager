"""Independent self-hosted encrypted sync relay (no local manager authority).

TLS authenticates transport, not freshness against a malicious relay. Clients must
validate signatures and their independently trusted membership/head checkpoints.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
import re
import sqlite3
import stat
import time
from contextlib import closing, contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import psutil
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import SecretStr

from camoufox_pm.core.update_permissions import (
    create_private_directory,
    create_private_file,
    reject_reparse_paths,
    verify_private_path,
)
from camoufox_pm.sync.identity import (
    DeviceCertificate,
    OwnerRoot,
    SignedMembership,
    canonical_bytes,
    decode_key,
    digest,
    normalize_relay_url,
    parse_record,
    verify_membership,
)
from camoufox_pm.sync.protocol import MAX_CIPHERTEXT_BYTES, parse_manifest, signing_bytes

_DOMAIN = b"camoufox-pm/sync/experimental-v1/relay/"
_EMPTY_HASH = hashlib.sha256(b"").hexdigest()
_MAX_JSON = 512 * 1024
_MAX_COUNTER = 2**53 - 1
_ERROR = "Sync relay request rejected"


def _json(obj: Any) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode()


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(_ERROR)
        result[key] = value
    return result


def _object(raw: bytes) -> dict[str, Any]:
    if len(raw) > _MAX_JSON:
        raise ValueError(_ERROR)
    value = json.loads(raw, object_pairs_hook=_pairs)
    if type(value) is not dict:
        raise ValueError(_ERROR)
    return value


def _page(rows: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    selected = []
    size = 1024
    for row in rows:
        size += len(row[1].encode()) + 128
        if size > _MAX_JSON:
            break
        selected.append(row)
    if rows and not selected:
        raise ValueError(_ERROR)
    return selected


def _id(value: str) -> str:
    if type(value) is not str or str(UUID(value)) != value or UUID(value).int == 0:
        raise ValueError(_ERROR)
    return value


def _counter(value: Any, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= _MAX_COUNTER:
        raise ValueError(_ERROR)
    return value


def registration_signing_bytes(root: OwnerRoot, membership: SignedMembership) -> bytes:
    return _DOMAIN + b"register\x00" + canonical_bytes(root) + b"\x00" + canonical_bytes(membership)


def request_signing_bytes(
    relay_url: str,
    method: str,
    path: str,
    owner_id: str,
    device_id: str,
    timestamp: int,
    nonce: str,
    body_sha256: str,
) -> bytes:
    """Canonical request binding; query strings are forbidden on relay routes."""
    _id(owner_id)
    _id(device_id)
    _id(nonce)
    _counter(timestamp)
    if method not in {"POST", "GET", "PUT"} or not re.fullmatch(r"[0-9a-f]{64}", body_sha256):
        raise ValueError(_ERROR)
    return (
        _DOMAIN
        + b"request\x00"
        + _json(
            {
                "relay_url": relay_url,
                "method": method,
                "path": path,
                "owner_id": owner_id,
                "device_id": device_id,
                "timestamp": timestamp,
                "nonce": nonce,
                "body_sha256": body_sha256,
            }
        )
    )


def takeover_signing_bytes(
    owner_id: str, profile_id: str, expected: dict[str, Any], operation_id: str, device_id: str
) -> bytes:
    """Explicit owner-authority approval; revocation cannot stop the old browser."""
    for identifier in (owner_id, profile_id, operation_id, device_id):
        _id(identifier)
    return (
        _DOMAIN
        + b"takeover\x00"
        + _json(
            {
                "owner_id": owner_id,
                "profile_id": profile_id,
                "expected": expected,
                "operation_id": operation_id,
                "device_id": device_id,
                "warning_ack": True,
            }
        )
    )


@dataclass(frozen=True)
class _Auth:
    owner_id: str
    device_id: str
    certificate: DeviceCertificate
    receipt: dict[str, Any]


class _LimitedFileResponse(FileResponse):
    """Hold a bounded download slot until completion, disconnect or deadline."""

    def __init__(self, *args, slots: asyncio.Semaphore, deadline: float = 1800, **kwargs):
        super().__init__(*args, **kwargs)
        self.slots = slots
        self.deadline = deadline

    async def __call__(self, scope, receive, send):
        try:
            await asyncio.wait_for(self.slots.acquire(), timeout=0.1)
        except asyncio.TimeoutError:
            await JSONResponse(status_code=429, content={"detail": _ERROR})(scope, receive, send)
            return
        try:
            await asyncio.wait_for(super().__call__(scope, receive, send), timeout=self.deadline)
        except asyncio.TimeoutError:
            # Do not append JSON to a partially sent ciphertext body. Closing the
            # incomplete response makes the client retain its pending operation.
            return
        finally:
            self.slots.release()


class _Store:
    def __init__(self, directory: Path):
        self.directory = directory.absolute()
        reject_reparse_paths(self.directory)
        if not self.directory.exists():
            create_private_directory(self.directory)
        verify_private_path(self.directory)
        self.blobs = self.directory / "blobs"
        if not self.blobs.exists():
            create_private_directory(self.blobs)
        verify_private_path(self.blobs)
        self.path = self.directory / "relay.sqlite3"
        created = not self.path.exists()
        if created:
            os.close(create_private_file(self.path))
        self._paths()
        with closing(self.connection()) as db:
            if created:
                db.executescript("""BEGIN IMMEDIATE;
CREATE TABLE owners (id TEXT PRIMARY KEY, root TEXT NOT NULL, membership TEXT NOT NULL);
CREATE TABLE memberships (owner TEXT NOT NULL, epoch INTEGER NOT NULL, document TEXT NOT NULL, PRIMARY KEY(owner,epoch));
CREATE TABLE profiles (owner TEXT NOT NULL, id TEXT NOT NULL, generation INTEGER NOT NULL, head TEXT, holder TEXT, PRIMARY KEY(owner,id));
CREATE TABLE revisions (owner TEXT NOT NULL, id TEXT NOT NULL, writer TEXT NOT NULL, size INTEGER NOT NULL, hash TEXT NOT NULL, state TEXT NOT NULL, manifest TEXT, signature TEXT, PRIMARY KEY(owner,id));
CREATE TABLE active_uploads (owner TEXT NOT NULL, revision TEXT NOT NULL, pid INTEGER NOT NULL, created REAL NOT NULL, PRIMARY KEY(owner,revision));
CREATE TABLE clock (id INTEGER PRIMARY KEY CHECK(id=1), highwater INTEGER NOT NULL);
INSERT INTO clock VALUES(1,0);
CREATE TABLE nonces (owner TEXT NOT NULL, nonce TEXT NOT NULL, timestamp INTEGER NOT NULL, PRIMARY KEY(owner,nonce));
CREATE TABLE events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, profile TEXT NOT NULL, receipt TEXT NOT NULL);
CREATE INDEX event_profile ON events(owner,profile,sequence);
PRAGMA application_id=1129336146;
PRAGMA user_version=1;
COMMIT;""")
            self._schema(db)
            if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise ValueError(_ERROR)
        self.sync_directory(self.directory)

    def _paths(self) -> None:
        verify_private_path(self.directory)
        verify_private_path(self.blobs)
        for suffix in ("", "-journal", "-wal", "-shm"):
            path = Path(str(self.path) + suffix)
            reject_reparse_paths(path)
            try:
                info = path.lstat()
            except FileNotFoundError:
                if not suffix:
                    raise
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError(_ERROR)
            try:
                verify_private_path(path, require_protected=not suffix)
            except FileNotFoundError:
                if not suffix:
                    raise
            if suffix in {"-wal", "-shm"}:
                raise ValueError(_ERROR)

    @staticmethod
    def _schema(db: sqlite3.Connection) -> None:
        if (
            db.execute("PRAGMA user_version").fetchone()[0] != 1
            or db.execute("PRAGMA application_id").fetchone()[0] != 1129336146
        ):
            raise ValueError(_ERROR)
        actual = db.execute(
            "SELECT type,name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        if actual != [
            ("table", "active_uploads"),
            ("table", "clock"),
            ("index", "event_profile"),
            ("table", "events"),
            ("table", "memberships"),
            ("table", "nonces"),
            ("table", "owners"),
            ("table", "profiles"),
            ("table", "revisions"),
        ]:
            raise ValueError(_ERROR)

    def connection(self) -> sqlite3.Connection:
        self._paths()
        db = sqlite3.connect(
            self.path.as_uri() + "?mode=rw", uri=True, isolation_level=None, timeout=10
        )
        db.execute("PRAGMA trusted_schema=OFF")
        db.execute("PRAGMA synchronous=FULL")
        if db.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
            db.close()
            raise ValueError(_ERROR)
        return db

    @contextmanager
    def transaction(self):
        with closing(self.connection()) as db:
            self._schema(db)
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    @staticmethod
    def sync_directory(path: Path) -> None:
        if os.name != "nt":
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def blob_path(self, owner: str, revision: str) -> Path:
        self._paths()
        path = self.blobs / (_id(owner) + "-" + _id(revision) + ".blob")
        reject_reparse_paths(path)
        return path

    def pending_path(self, owner: str, revision: str) -> Path:
        self._paths()
        path = self.blobs / (".pending-" + _id(owner) + "-" + _id(revision))
        reject_reparse_paths(path)
        return path

    @staticmethod
    def owner(db: sqlite3.Connection, owner_id: str) -> tuple[OwnerRoot, SignedMembership]:
        row = db.execute("SELECT root,membership FROM owners WHERE id=?", (owner_id,)).fetchone()
        if row is None:
            raise HTTPException(403, _ERROR)
        root = parse_record(row[0].encode(), OwnerRoot)
        membership = parse_record(row[1].encode(), SignedMembership)
        verify_membership(root, membership, trusted_digest=digest(membership))
        return root, membership

    @staticmethod
    def device(membership: SignedMembership, device_id: str):
        for device in membership.membership.devices:
            if device.device_id == device_id:
                return device
        raise HTTPException(403, _ERROR)

    @staticmethod
    def checkpoint(db: sqlite3.Connection, owner: str, profile: str, epoch: int) -> dict[str, Any]:
        row = db.execute(
            "SELECT generation,head,holder FROM profiles WHERE owner=? AND id=?", (owner, profile)
        ).fetchone()
        if row is None:
            generation, head, holder = 0, None, None
        else:
            generation, head, holder = row
        return {
            "owner_id": owner,
            "profile_id": profile,
            "membership_epoch": epoch,
            "checkout_generation": generation,
            "revision_id": head,
            "holder_device_id": holder,
        }


def create_relay_app(
    directory: Path,
    *,
    relay_url: str,
    registration_token: SecretStr,
    development_loopback: bool = False,
    max_blob_bytes: int = MAX_CIPHERTEXT_BYTES,
    owner_quota_bytes: int = 10 * 1024**3,
    max_owners: int = 100,
) -> FastAPI:
    """Create a separate relay ASGI app. Run behind a correctly configured TLS proxy."""
    origin = normalize_relay_url(relay_url, allow_loopback_http=development_loopback)
    if (
        not isinstance(registration_token, SecretStr)
        or len(registration_token.get_secret_value()) < 32
        or type(max_blob_bytes) is not int
        or not 41 <= max_blob_bytes <= MAX_CIPHERTEXT_BYTES
        or type(owner_quota_bytes) is not int
        or not max_blob_bytes <= owner_quota_bytes <= 1024**5
        or type(max_owners) is not int
        or not 1 <= max_owners <= 10000
    ):
        raise ValueError("Invalid sync relay configuration")
    try:
        store = _Store(directory)
    except Exception:
        raise ValueError("Protected sync relay storage is unavailable") from None
    app = FastAPI(title="Camoufox PM sync relay", docs_url=None, redoc_url=None, openapi_url=None)
    streams = asyncio.Semaphore(4)
    downloads = asyncio.Semaphore(4)
    small_requests = asyncio.Semaphore(8)

    @app.exception_handler(Exception)
    async def failure(request: Request, exc: Exception):
        return JSONResponse(status_code=400, content={"detail": _ERROR})

    @app.middleware("http")
    async def transport(request: Request, call_next):
        allow_http = development_loopback and origin.startswith("http://")
        if request.url.scheme != "https" and allow_http:
            try:
                allow_http = (
                    request.client is not None
                    and ipaddress.ip_address(request.client.host).is_loopback
                )
            except ValueError:
                allow_http = False
        if request.url.query or (request.url.scheme != "https" and not allow_http):
            return JSONResponse(status_code=400, content={"detail": _ERROR})
        try:
            return await call_next(request)
        except Exception:
            return JSONResponse(status_code=400, content={"detail": _ERROR})

    async def incoming(request: Request, budget: float = 1800):
        iterator = request.stream().__aiter__()
        deadline = time.monotonic() + budget
        while True:
            try:
                remaining = min(30.0, deadline - time.monotonic())
                if remaining <= 0:
                    raise HTTPException(408, _ERROR)
                part = await asyncio.wait_for(iterator.__anext__(), timeout=remaining)
            except StopAsyncIteration:
                return
            except asyncio.TimeoutError:
                raise HTTPException(408, _ERROR) from None
            yield part

    async def small_body(request: Request) -> bytes:
        try:
            await asyncio.wait_for(small_requests.acquire(), timeout=0.1)
        except asyncio.TimeoutError:
            raise HTTPException(429, _ERROR) from None
        try:
            result = bytearray()
            async for part in incoming(request, budget=60):
                if len(result) + len(part) > _MAX_JSON:
                    raise HTTPException(413, _ERROR)
                result.extend(part)
            return bytes(result)
        finally:
            small_requests.release()

    def authenticate(request: Request, owner_id: str, body_hash: str) -> _Auth:
        _id(owner_id)
        headers = request.headers
        for name in (
            "x-sync-device",
            "x-sync-nonce",
            "x-sync-time",
            "x-sync-body-sha256",
            "x-sync-signature",
        ):
            if len(headers.getlist(name)) != 1:
                raise HTTPException(403, _ERROR)
        device_id = _id(headers.get("x-sync-device", ""))
        nonce = _id(headers.get("x-sync-nonce", ""))
        raw_time = headers.get("x-sync-time", "")
        if not re.fullmatch(r"[0-9]{1,16}", raw_time):
            raise HTTPException(403, _ERROR)
        timestamp = int(raw_time)
        now = int(time.time())
        if abs(now - timestamp) > 300 or headers.get("x-sync-body-sha256") != body_hash:
            raise HTTPException(403, _ERROR)
        signature = decode_key(headers.get("x-sync-signature", ""), 64)
        message = request_signing_bytes(
            origin,
            request.method,
            request.url.path,
            owner_id,
            device_id,
            timestamp,
            nonce,
            body_hash,
        )
        with store.transaction() as db:
            _, membership = store.owner(db, owner_id)
            device = store.device(membership, device_id)
            Ed25519PublicKey.from_public_bytes(decode_key(device.signing_public)).verify(
                signature, message
            )
            highwater = db.execute("SELECT highwater FROM clock WHERE id=1").fetchone()
            if highwater is None or now < highwater[0]:
                raise HTTPException(503, _ERROR)
            db.execute("UPDATE clock SET highwater=? WHERE id=1", (now,))
            db.execute("DELETE FROM nonces WHERE timestamp<?", (now - 300,))
            if (
                db.execute("SELECT count(*) FROM nonces WHERE owner=?", (owner_id,)).fetchone()[0]
                >= 4096
            ):
                raise HTTPException(429, _ERROR)
            try:
                db.execute("INSERT INTO nonces VALUES(?,?,?)", (owner_id, nonce, timestamp))
            except sqlite3.IntegrityError:
                raise HTTPException(409, _ERROR) from None
        return _Auth(
            owner_id,
            device_id,
            device,
            {
                "relay_url": origin,
                "method": request.method,
                "path": request.url.path,
                "owner_id": owner_id,
                "device_id": device_id,
                "timestamp": timestamp,
                "nonce": nonce,
                "body_sha256": body_hash,
                "signature": headers["x-sync-signature"],
            },
        )

    def current_device(membership: SignedMembership, auth: _Auth):
        device = store.device(membership, auth.device_id)
        if device != auth.certificate:
            raise HTTPException(403, _ERROR)
        return device

    @app.post("/v1/register")
    async def register(request: Request):
        supplied = request.headers.get("x-registration-token", "")
        if not hmac.compare_digest(
            supplied.encode(), registration_token.get_secret_value().encode()
        ):
            raise HTTPException(403, _ERROR)
        obj = _object(await small_body(request))
        if set(obj) != {"root", "membership", "signature"}:
            raise HTTPException(400, _ERROR)
        root = parse_record(_json(obj["root"]), OwnerRoot)
        membership = parse_record(_json(obj["membership"]), SignedMembership)
        if root.relay_url != origin or root.development_loopback != development_loopback:
            raise HTTPException(400, _ERROR)
        # Authenticate the submitted authority even for an existing owner. A
        # reconnect may carry a later pinned membership; only new registration
        # must start at genesis.
        verify_membership(root, membership, trusted_digest=digest(membership))
        Ed25519PublicKey.from_public_bytes(decode_key(root.authority_public)).verify(
            decode_key(obj["signature"], 64), registration_signing_bytes(root, membership)
        )
        with store.transaction() as db:
            if db.execute("SELECT 1 FROM owners WHERE id=?", (root.owner_id,)).fetchone():
                raise HTTPException(409, _ERROR)
            verify_membership(root, membership)
            if db.execute("SELECT count(*) FROM owners").fetchone()[0] >= max_owners:
                raise HTTPException(429, _ERROR)
            db.execute(
                "INSERT INTO owners VALUES(?,?,?)",
                (
                    root.owner_id,
                    canonical_bytes(root).decode(),
                    canonical_bytes(membership).decode(),
                ),
            )
            db.execute(
                "INSERT INTO memberships VALUES(?,?,?)",
                (root.owner_id, 1, canonical_bytes(membership).decode()),
            )
        return {"owner_id": root.owner_id, "membership_digest": digest(membership)}

    @app.post("/v1/owners/{owner_id}/rpc")
    async def rpc(owner_id: str, request: Request):
        # Authenticate the signed body commitment before buffering any bytes.
        committed_hash = request.headers.get("x-sync-body-sha256", "")
        auth = authenticate(request, owner_id, committed_hash)
        raw = await small_body(request)
        if hashlib.sha256(raw).hexdigest() != committed_hash:
            raise HTTPException(403, _ERROR)
        obj = _object(raw)
        operation = obj.get("operation")
        allowed = {
            "membership": {"operation"},
            "update_membership": {"operation", "membership"},
            "checkpoint": {"operation", "profile_id"},
            "claim": {"operation", "profile_id", "expected"},
            "release": {"operation", "profile_id", "expected"},
            "takeover": {
                "operation",
                "profile_id",
                "expected",
                "operation_id",
                "warning_ack",
                "authority_signature",
            },
            "commit": {"operation", "profile_id", "manifest", "signature", "expected"},
            "manifest": {"operation", "revision_id"},
            "history": {"operation", "profile_id", "after_sequence"},
            "memberships": {"operation", "after_epoch"},
            "discard_upload": {"operation", "revision_id"},
            "blob_status": {"operation", "revision_id"},
        }
        if type(operation) is not str or operation not in allowed or set(obj) != allowed[operation]:
            raise HTTPException(400, _ERROR)
        with store.transaction() as db:
            root, membership = store.owner(db, owner_id)
            device = current_device(membership, auth)
            epoch = membership.membership.epoch
            if operation == "membership":
                return {
                    "root": root.model_dump(mode="json"),
                    "membership": membership.model_dump(mode="json"),
                }
            if operation == "memberships":
                after = _counter(obj["after_epoch"])
                rows = db.execute(
                    "SELECT epoch,document FROM memberships WHERE owner=? AND epoch>? ORDER BY epoch LIMIT 128",
                    (owner_id, after),
                ).fetchall()
                rows = _page(rows)
                return {
                    "memberships": [json.loads(row[1]) for row in rows],
                    "last_epoch": rows[-1][0] if rows else after,
                }
            if operation == "blob_status":
                revision = _id(obj["revision_id"])
                row = db.execute(
                    "SELECT writer,hash,size,state FROM revisions WHERE owner=? AND id=?",
                    (owner_id, revision),
                ).fetchone()
                if row is None:
                    raise HTTPException(404, _ERROR)
                if row[0] != auth.device_id:
                    raise HTTPException(403, _ERROR)
                return {
                    "revision_id": revision,
                    "writer_device_id": row[0],
                    "ciphertext_sha256": row[1],
                    "ciphertext_length": row[2],
                    "state": row[3],
                }
            if operation == "discard_upload":
                revision = _id(obj["revision_id"])
                row = db.execute(
                    "SELECT writer,state FROM revisions WHERE owner=? AND id=?",
                    (owner_id, revision),
                ).fetchone()
                if row is None or row[0] != auth.device_id or row[1] == "committed":
                    raise HTTPException(409, _ERROR)
                active = db.execute(
                    "SELECT pid,created FROM active_uploads WHERE owner=? AND revision=?",
                    (owner_id, revision),
                ).fetchone()
                if active is not None:
                    try:
                        if abs(psutil.Process(active[0]).create_time() - active[1]) < 0.01:
                            raise HTTPException(409, _ERROR)
                    except psutil.NoSuchProcess:
                        pass
                    except psutil.AccessDenied:
                        raise HTTPException(409, _ERROR) from None
                db.execute(
                    "UPDATE revisions SET state='aborted',size=0 WHERE owner=? AND id=?",
                    (owner_id, revision),
                )
                path = store.blob_path(owner_id, revision)
                pending = store.pending_path(owner_id, revision)
                pending.unlink(missing_ok=True)
                path.unlink(missing_ok=True)
                store.sync_directory(store.blobs)
                db.execute(
                    "DELETE FROM active_uploads WHERE owner=? AND revision=?", (owner_id, revision)
                )
                return {"revision_id": revision, "state": "aborted"}
            if operation == "update_membership":
                if epoch >= 10000:
                    raise HTTPException(429, _ERROR)
                successor = parse_record(_json(obj["membership"]), SignedMembership)
                verify_membership(root, successor, previous=membership)
                used_memberships = db.execute(
                    "SELECT coalesce(sum(length(document)),0) FROM memberships WHERE owner=?",
                    (owner_id,),
                ).fetchone()[0]
                if used_memberships + len(canonical_bytes(successor)) > 64 * 1024**2:
                    raise HTTPException(429, _ERROR)
                db.execute(
                    "UPDATE owners SET membership=? WHERE id=?",
                    (canonical_bytes(successor).decode(), owner_id),
                )
                db.execute(
                    "INSERT INTO memberships VALUES(?,?,?)",
                    (owner_id, successor.membership.epoch, canonical_bytes(successor).decode()),
                )
                return {"membership": successor.model_dump(mode="json")}
            if operation == "manifest":
                revision = _id(obj["revision_id"])
                row = db.execute(
                    "SELECT manifest,signature FROM revisions WHERE owner=? AND id=? AND state='committed'",
                    (owner_id, revision),
                ).fetchone()
                if row is None:
                    raise HTTPException(404, _ERROR)
                return {"manifest": json.loads(row[0]), "signature": row[1]}
            profile = _id(obj["profile_id"])
            checkpoint = store.checkpoint(db, owner_id, profile, epoch)
            if operation == "checkpoint":
                return {"checkpoint": checkpoint}
            if operation == "history":
                after = _counter(obj["after_sequence"])
                rows = db.execute(
                    "SELECT sequence,receipt FROM events WHERE owner=? AND profile=? AND sequence>? ORDER BY sequence LIMIT 128",
                    (owner_id, profile, after),
                ).fetchall()
                rows = _page(rows)
                return {
                    "events": [{"sequence": row[0], "receipt": json.loads(row[1])} for row in rows],
                    "last_sequence": rows[-1][0] if rows else after,
                }
            if type(obj["expected"]) is not dict or _json(obj["expected"]) != _json(checkpoint):
                raise HTTPException(409, _ERROR)
            generation = checkpoint["checkout_generation"]
            if operation == "claim":
                if checkpoint["holder_device_id"] is not None or generation >= _MAX_COUNTER:
                    raise HTTPException(409, _ERROR)
                if (
                    generation == 0
                    and db.execute(
                        "SELECT count(*) FROM profiles WHERE owner=?", (owner_id,)
                    ).fetchone()[0]
                    >= 1024
                ):
                    raise HTTPException(429, _ERROR)
                db.execute(
                    "INSERT INTO profiles VALUES(?,?,?,?,?) ON CONFLICT(owner,id) DO UPDATE SET generation=excluded.generation,holder=excluded.holder",
                    (owner_id, profile, generation + 1, checkpoint["revision_id"], auth.device_id),
                )
            elif operation == "takeover":
                if (
                    obj["warning_ack"] is not True
                    or checkpoint["holder_device_id"] is None
                    or checkpoint["holder_device_id"]
                    in {d.device_id for d in membership.membership.devices}
                    or generation >= _MAX_COUNTER
                ):
                    raise HTTPException(409, _ERROR)
                Ed25519PublicKey.from_public_bytes(decode_key(root.authority_public)).verify(
                    decode_key(obj["authority_signature"], 64),
                    takeover_signing_bytes(
                        owner_id, profile, checkpoint, obj["operation_id"], auth.device_id
                    ),
                )
                db.execute(
                    "UPDATE profiles SET holder=?,generation=generation+1 WHERE owner=? AND id=?",
                    (auth.device_id, owner_id, profile),
                )
            elif operation == "release":
                if checkpoint["holder_device_id"] != auth.device_id:
                    raise HTTPException(409, _ERROR)
                db.execute(
                    "UPDATE profiles SET holder=NULL WHERE owner=? AND id=?", (owner_id, profile)
                )
            elif operation == "commit":
                manifest = parse_manifest(_json(obj["manifest"]))
                if (
                    checkpoint["holder_device_id"] != auth.device_id
                    or manifest.owner_id != owner_id
                    or manifest.profile_id != profile
                    or manifest.writer_device_id != auth.device_id
                    or manifest.membership_epoch != epoch
                    or manifest.checkout_generation != generation
                    or manifest.parent_revision_id != checkpoint["revision_id"]
                ):
                    raise HTTPException(409, _ERROR)
                recipients = sorted(
                    [("device", d.device_id) for d in membership.membership.devices]
                    + [("recovery", root.recovery_id)]
                )
                if [(r.kind, r.recipient_id) for r in manifest.recipients] != recipients:
                    raise HTTPException(400, _ERROR)
                Ed25519PublicKey.from_public_bytes(decode_key(device.signing_public)).verify(
                    decode_key(obj["signature"], 64), signing_bytes(manifest)
                )
                blob = db.execute(
                    "SELECT writer,size,hash,state FROM revisions WHERE owner=? AND id=?",
                    (owner_id, manifest.revision_id),
                ).fetchone()
                if blob != (
                    auth.device_id,
                    manifest.ciphertext_length,
                    manifest.ciphertext_sha256,
                    "ready",
                ):
                    raise HTTPException(409, _ERROR)
                blob_path = store.blob_path(owner_id, manifest.revision_id)
                verify_private_path(blob_path)
                info = blob_path.lstat()
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or info.st_size != manifest.ciphertext_length
                ):
                    raise HTTPException(409, _ERROR)
                db.execute(
                    "UPDATE revisions SET state='committed',manifest=?,signature=? WHERE owner=? AND id=?",
                    (
                        _json(obj["manifest"]).decode(),
                        obj["signature"],
                        owner_id,
                        manifest.revision_id,
                    ),
                )
                db.execute(
                    "UPDATE profiles SET head=? WHERE owner=? AND id=?",
                    (manifest.revision_id, owner_id, profile),
                )
            used_events = db.execute(
                "SELECT coalesce(sum(length(receipt)),0) FROM events WHERE owner=?", (owner_id,)
            ).fetchone()[0]
            if used_events + len(raw) + 4096 > 64 * 1024**2:
                raise HTTPException(429, _ERROR)
            receipt = dict(auth.receipt, body=raw.decode())
            db.execute(
                "INSERT INTO events(owner,profile,receipt) VALUES(?,?,?)",
                (owner_id, profile, _json(receipt).decode()),
            )
            return {
                "checkpoint": store.checkpoint(db, owner_id, profile, epoch),
                "receipt": receipt,
            }

    @app.put("/v1/owners/{owner_id}/blobs/{revision_id}")
    async def upload(owner_id: str, revision_id: str, request: Request):
        _id(revision_id)
        length_text = request.headers.get("content-length", "")
        body_hash = request.headers.get("x-sync-body-sha256", "")
        if not re.fullmatch(r"[0-9]{1,12}", length_text):
            raise HTTPException(411, _ERROR)
        length = int(length_text)
        if not 41 <= length <= max_blob_bytes:
            raise HTTPException(413, _ERROR)
        auth = authenticate(request, owner_id, body_hash)
        try:
            await asyncio.wait_for(streams.acquire(), timeout=0.1)
        except asyncio.TimeoutError:
            raise HTTPException(429, _ERROR) from None
        try:
            with store.transaction() as db:
                _, membership = store.owner(db, owner_id)
                current_device(membership, auth)
                used, count = db.execute(
                    "SELECT coalesce(sum(size),0),count(*) FROM revisions WHERE owner=?",
                    (owner_id,),
                ).fetchone()
                if used + length > owner_quota_bytes or count >= 10000:
                    raise HTTPException(429, _ERROR)
                try:
                    db.execute(
                        "INSERT INTO revisions VALUES(?,?,?,?,?,'uploading',NULL,NULL)",
                        (owner_id, revision_id, auth.device_id, length, body_hash),
                    )
                    process = psutil.Process()
                    db.execute(
                        "INSERT INTO active_uploads VALUES(?,?,?,?)",
                        (owner_id, revision_id, process.pid, process.create_time()),
                    )
                except sqlite3.IntegrityError:
                    raise HTTPException(409, _ERROR) from None
            destination = store.blob_path(owner_id, revision_id)
            temporary = store.pending_path(owner_id, revision_id)
            published = False
            try:
                checksum = hashlib.sha256()
                received = 0
                fd = create_private_file(temporary)
                with os.fdopen(fd, "wb") as output:
                    async for part in incoming(request):
                        received += len(part)
                        if received > length:
                            raise HTTPException(413, _ERROR)
                        checksum.update(part)
                        output.write(part)
                    if received != length or checksum.hexdigest() != body_hash:
                        raise HTTPException(400, _ERROR)
                    output.flush()
                    os.fsync(output.fileno())
                verify_private_path(temporary)
                reject_reparse_paths(destination)
                os.link(temporary, destination, follow_symlinks=False)
                published = True
                temporary.unlink()
                store.sync_directory(store.blobs)
                with store.transaction() as db:
                    _, membership = store.owner(db, owner_id)
                    current_device(membership, auth)
                    cursor = db.execute(
                        "UPDATE revisions SET state='ready' WHERE owner=? AND id=? AND state='uploading'",
                        (owner_id, revision_id),
                    )
                    if cursor.rowcount != 1:
                        raise HTTPException(409, _ERROR)
                    db.execute(
                        "DELETE FROM active_uploads WHERE owner=? AND revision=?",
                        (owner_id, revision_id),
                    )
                return {
                    "revision_id": revision_id,
                    "ciphertext_sha256": body_hash,
                    "ciphertext_length": received,
                }
            except BaseException:
                with suppress(Exception):
                    temporary.unlink(missing_ok=True)
                    if published:
                        destination.unlink(missing_ok=True)
                    with store.transaction() as db:
                        db.execute(
                            "UPDATE revisions SET state='aborted',size=0 WHERE owner=? AND id=? AND state='uploading'",
                            (owner_id, revision_id),
                        )
                        db.execute(
                            "DELETE FROM active_uploads WHERE owner=? AND revision=?",
                            (owner_id, revision_id),
                        )
                raise
        finally:
            streams.release()

    @app.get("/v1/owners/{owner_id}/blobs/{revision_id}")
    async def download(owner_id: str, revision_id: str, request: Request):
        _id(revision_id)
        auth = authenticate(request, owner_id, _EMPTY_HASH)
        async for part in incoming(request):
            if part:
                raise HTTPException(400, _ERROR)
        with store.transaction() as db:
            _, membership = store.owner(db, owner_id)
            current_device(membership, auth)
            row = db.execute(
                "SELECT size,hash FROM revisions WHERE owner=? AND id=? AND state='committed'",
                (owner_id, revision_id),
            ).fetchone()
            if row is None:
                raise HTTPException(404, _ERROR)
        path = store.blob_path(owner_id, revision_id)
        verify_private_path(path)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != row[0]:
            raise HTTPException(400, _ERROR)
        return _LimitedFileResponse(
            path,
            slots=downloads,
            media_type="application/octet-stream",
            headers={"X-Sync-Body-SHA256": row[1], "Cache-Control": "no-store"},
        )

    return app
