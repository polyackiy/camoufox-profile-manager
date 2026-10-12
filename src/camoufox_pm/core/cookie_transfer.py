"""Portable cookie transfer through browser APIs and atomically staged stores.

Firefox creates/migrates its own schema. Transfers never fabricate moz_cookies
columns or convert Firefox timestamps. A separate table carries session cookies
until the next launch; the launch hook injects them once through Playwright.
"""

from __future__ import annotations

import ipaddress
import json
import math
import os
import re
import sqlite3
import tempfile
import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlsplit

from camoufox.addons import DefaultAddons
from camoufox.async_api import AsyncCamoufox
from playwright.async_api import BrowserContext

from .browser_install import ensure_browser_ready
from .database import StaleWriteError
from .leases import make_lease_holder
from .profile_operations import profile_data_path, profile_operation, run_file_operation

MAX_BYTES = 2 * 1024 * 1024
MAX_COOKIES = 10_000
CookieFormat = Literal["json", "netscape"]
_PENDING_TABLE = "cpm_cookie_transfer"
_ACK_NAME = ".cookie-transfer-ack.json"
_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


class CookieTransferError(ValueError):
    """Credential-safe error: messages never include cookie input."""


@dataclass
class CookieBatch:
    cookies: list[dict[str, Any]]
    total: int
    skipped: list[dict[str, Any]]

    def preview(self) -> dict:
        return {
            "total": self.total,
            "importable": len(self.cookies),
            "session_count": sum(cookie["expires"] == -1 for cookie in self.cookies),
            "skipped": self.skipped,
        }


def _fail(row: int, reason: str):
    raise CookieTransferError(f"Row {row}: {reason}")


def _domain(value: Any, row: int) -> str:
    if not isinstance(value, str) or not value or any(char.isspace() for char in value):
        _fail(row, "A cookie domain is required")
    host = value.lstrip(".")
    if any(char in host for char in "/@?#%\\:"):
        _fail(row, "Invalid cookie domain")
    try:
        host = host.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        _fail(row, "Invalid cookie domain")
    if len(host) > 253 or not all(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split(".")
    ):
        _fail(row, "Invalid cookie domain")
    return ("." if value.startswith(".") else "") + host


def _boolean(value: Any, row: int) -> bool:
    if not isinstance(value, bool):
        _fail(row, "Cookie flags must be true or false")
    return value


def _normalize(item: Any, row: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        _fail(row, "Each cookie must be an object")
    if (
        item.get("partitionKey") is not None
        or item.get("partitioned")
        or item.get("firstPartyDomain")
    ):
        _fail(row, "Partitioned cookies need a full profile archive")
    if item.get("originAttributes") or item.get("storeId") not in (None, "0", "firefox-default"):
        _fail(row, "Container cookies need a full profile archive")
    name, value = item.get("name"), item.get("value")
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        _fail(row, "Invalid cookie name")
    if not isinstance(value, str) or any(ord(char) < 32 or ord(char) == 127 for char in value):
        _fail(row, "Invalid cookie value")
    try:
        value_size = len((name + value).encode("utf-8"))
    except UnicodeError:
        _fail(row, "Cookie value contains invalid Unicode")
    if value_size > 4096:
        _fail(row, "Cookie name and value exceed 4096 bytes")
    domain = item.get("domain")
    url_path, url_secure = "/", False
    if "url" in item:
        try:
            raw_url = item["url"]
            if (
                not isinstance(raw_url, str)
                or domain is not None
                or "path" in item
                or any(ord(char) <= 32 or ord(char) == 127 for char in raw_url)
            ):
                raise ValueError
            url = urlsplit(raw_url)
            if url.scheme not in ("http", "https") or url.username is not None or not url.hostname:
                raise ValueError
            _ = url.port
            domain = url.hostname
            url_path = url.path[: url.path.rfind("/") + 1] or "/"
            url_secure = url.scheme == "https"
        except ValueError:
            _fail(row, "Invalid cookie URL")
    domain = _domain(domain, row)
    host_only = _boolean(item.get("hostOnly", not domain.startswith(".")), row)
    domain = domain.lstrip(".") if host_only else "." + domain.lstrip(".")
    if not host_only:
        if "." not in domain.lstrip("."):
            _fail(row, "Single-label cookie domains must be host-only")
        try:
            ipaddress.ip_address(domain.lstrip("."))
        except ValueError:
            pass
        else:
            _fail(row, "IP-address cookies must be host-only")
    path = item.get("path", url_path)
    if not isinstance(path, str) or not path.startswith("/"):
        _fail(row, "Cookie path must begin with a slash")
    try:
        path_size = len(path.encode("utf-8"))
    except UnicodeError:
        _fail(row, "Cookie path contains invalid Unicode")
    if path_size > 1024:
        _fail(row, "Cookie path must begin with a slash and fit within 1024 bytes")
    if any(ord(char) < 32 or ord(char) == 127 for char in path):
        _fail(row, "Invalid cookie path")
    secure = _boolean(item.get("secure", False), row)
    if "url" in item:
        secure = url_secure
    http_only = _boolean(item.get("httpOnly", False), row)
    raw_same_site = item.get("sameSite", "Lax")
    sites = {
        "strict": "Strict",
        "lax": "Lax",
        "none": "None",
        "no_restriction": "None",
        "unspecified": "Lax",
    }
    if not isinstance(raw_same_site, str) or raw_same_site.lower() not in sites:
        _fail(row, "SameSite must be Strict, Lax or None")
    same_site = sites[raw_same_site.lower()]
    if same_site == "None" and not secure:
        _fail(row, "SameSite=None requires a secure cookie")
    if name.startswith("__Secure-") and not secure:
        _fail(row, "A __Secure- cookie must be secure")
    if name.startswith("__Host-") and (not secure or not host_only or path != "/"):
        _fail(row, "A __Host- cookie must be secure, host-only and use path /")
    session = _boolean(item["session"], row) if "session" in item else False
    expiry = -1 if session else item.get("expires", item.get("expirationDate", -1))
    if (
        isinstance(expiry, bool)
        or not isinstance(expiry, (int, float))
        or not math.isfinite(expiry)
    ):
        _fail(row, "Cookie expiry must be a finite Unix timestamp")
    if expiry != -1 and not 0 <= expiry <= 253402300799:
        _fail(row, "Invalid cookie expiry")
    return {
        "name": name,
        "value": value,
        "domain": domain,
        "path": path,
        "expires": expiry,
        "httpOnly": http_only,
        "secure": secure,
        "sameSite": same_site,
    }


def _key(cookie: dict) -> tuple:
    return cookie["name"], cookie["domain"], cookie["path"]


def parse_cookies(text: str, format: CookieFormat, *, now: float | None = None) -> CookieBatch:
    try:
        input_size = len(text.encode("utf-8"))
    except UnicodeError:
        raise CookieTransferError("Cookie input contains invalid Unicode") from None
    if input_size > MAX_BYTES:
        raise CookieTransferError("Cookie input exceeds 2 MiB")
    text = text.removeprefix("\ufeff")
    if format == "json":
        try:
            items = json.loads(text)
        except (ValueError, RecursionError):
            raise CookieTransferError("Cookie JSON must be an array of cookie objects") from None
        if not isinstance(items, list):
            raise CookieTransferError("Cookie JSON must be an array of cookie objects")
        rows = list(enumerate(items, 1))
    elif format == "netscape":
        rows = []
        for number, line in enumerate(text.splitlines(), 1):
            if not line.strip() or (line.startswith("#") and not line.startswith("#HttpOnly_")):
                continue
            fields = line.split("\t")
            if len(fields) != 7:
                _fail(number, "Netscape cookies require seven tab-separated fields")
            domain, include_subdomains, path, secure, expiry, name, value = fields
            http_only = domain.startswith("#HttpOnly_")
            domain = domain.removeprefix("#HttpOnly_")
            if include_subdomains.upper() not in ("TRUE", "FALSE") or secure.upper() not in (
                "TRUE",
                "FALSE",
            ):
                _fail(number, "Netscape flags must be TRUE or FALSE")
            if len(expiry) > 12 or not re.fullmatch(r"[0-9]+", expiry):
                _fail(number, "Netscape expiry must be a nonnegative Unix timestamp")
            rows.append(
                (
                    number,
                    {
                        "domain": domain,
                        "hostOnly": include_subdomains.upper() == "FALSE",
                        "path": path,
                        "secure": secure.upper() == "TRUE",
                        "httpOnly": http_only,
                        "expires": -1 if expiry == "0" else int(expiry),
                        "name": name,
                        "value": value,
                    },
                )
            )
    else:
        raise CookieTransferError("Select JSON or Netscape cookie format")
    if len(rows) > MAX_COOKIES:
        raise CookieTransferError("Import at most 10000 cookies")
    normalized = [(row, _normalize(item, row)) for row, item in rows]
    clock = time.time() if now is None else now
    selected: dict[tuple, tuple[int, dict]] = {}
    skipped = []
    for row, cookie in normalized:
        if cookie["expires"] != -1 and cookie["expires"] <= clock:
            skipped.append({"row": row, "reason": "Expired cookie"})
            continue
        key = _key(cookie)
        if key in selected:
            skipped.append(
                {"row": selected[key][0], "reason": "Duplicate cookie; later entry wins"}
            )
        selected[key] = row, cookie
    return CookieBatch([cookie for _, cookie in selected.values()], len(rows), skipped)


def serialize_cookies(cookies: list[dict], format: CookieFormat) -> str:
    if format == "json":
        return json.dumps(cookies, ensure_ascii=False, indent=2)
    lines = [
        "# Netscape HTTP Cookie File",
        "# Cookie values are sensitive. SameSite is not represented in this format.",
    ]
    for cookie in cookies:
        if any(
            "\t" in str(cookie[field]) or "\n" in str(cookie[field])
            for field in ("domain", "path", "name", "value")
        ):
            raise CookieTransferError("A cookie cannot be represented in Netscape format; use JSON")
        domain = ("#HttpOnly_" if cookie["httpOnly"] else "") + cookie["domain"]
        lines.append(
            "\t".join(
                [
                    domain,
                    "TRUE" if cookie["domain"].startswith(".") else "FALSE",
                    cookie["path"],
                    "TRUE" if cookie["secure"] else "FALSE",
                    "0" if cookie["expires"] == -1 else str(int(cookie["expires"])),
                    cookie["name"],
                    cookie["value"],
                ]
            )
        )
    return "\n".join(lines) + "\n"


def _read_pending(
    database: Path, acknowledgment: Path | None = None, *, honor_ack: bool = True
) -> dict | None:
    if not database.exists():
        return None
    if database.is_symlink():
        raise CookieTransferError("Cookie storage must not be a symbolic link")
    with closing(sqlite3.connect(database)) as connection:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (_PENDING_TABLE,)
        ).fetchone()
        if not exists:
            return None
        row = connection.execute(f"SELECT payload FROM {_PENDING_TABLE} WHERE id=1").fetchone()
        if not row:
            return None
        if not isinstance(row[0], str):
            raise CookieTransferError("Pending cookie data is invalid; restore a profile backup")
        if len(row[0].encode()) > MAX_BYTES:
            raise CookieTransferError("Pending cookie data exceeds the transfer limit")
        try:
            payload = json.loads(row[0])
            if (
                not isinstance(payload, dict)
                or set(payload) != {"nonce", "replace", "cookies"}
                or not isinstance(payload["replace"], bool)
                or not isinstance(payload["cookies"], list)
            ):
                raise ValueError
            if uuid.UUID(payload["nonce"]).hex != payload["nonce"]:
                raise ValueError
            parse_cookies(json.dumps(payload["cookies"]), "json")
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise CookieTransferError(
                "Pending cookie data is invalid; restore a profile backup"
            ) from None
        ack = acknowledgment or database.parent / _ACK_NAME
        if honor_ack and _read_ack(ack) == payload["nonce"]:
            return None
        return payload


def _set_pending(database: Path, payload: dict | None, *, checkpoint: bool = True):
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            f"CREATE TABLE IF NOT EXISTS {_PENDING_TABLE} (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)"
        )
        connection.execute(f"DELETE FROM {_PENDING_TABLE}")
        if payload:
            record = {**payload, "nonce": payload.get("nonce") or uuid.uuid4().hex}
            encoded = json.dumps(record, ensure_ascii=False)
            if len(encoded.encode("utf-8")) > MAX_BYTES:
                raise CookieTransferError(
                    "Cookie data exceeds 2 MiB after normalization; import a smaller batch"
                )
            connection.execute(f"INSERT INTO {_PENDING_TABLE} VALUES (1, ?)", (encoded,))
        connection.commit()
        if checkpoint and connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]:
            raise CookieTransferError("The staged cookie store is busy; retry the transfer")
    database.chmod(0o600)


def _copy_store(source: Path, destination: Path):
    if source.is_symlink():
        raise CookieTransferError("Cookie storage must not be a symbolic link")
    if source.exists():
        with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as original:
            with closing(sqlite3.connect(destination)) as target:
                original.backup(target)


def _publish_store(candidate: Path, target: Path):
    if target.is_symlink():
        raise CookieTransferError("Cookie storage must not be a symbolic link")
    if target.exists():
        with closing(sqlite3.connect(target, timeout=5)) as existing:
            if existing.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]:
                raise CookieTransferError("Cookie storage is busy; close the browser and retry")
            existing.execute("PRAGMA journal_mode=DELETE")
    candidate.chmod(0o600)
    candidate.replace(target)


def _read_ack(path: Path) -> str | None:
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_size > 128:
        raise CookieTransferError("Cookie transfer acknowledgment is invalid")
    try:
        nonce = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(nonce, str) or uuid.UUID(nonce).hex != nonce:
            raise ValueError
        return nonce
    except (ValueError, OSError, UnicodeError, TypeError):
        raise CookieTransferError("Cookie transfer acknowledgment is invalid") from None


def _write_ack(profile_dir: Path, nonce: str):
    target = profile_dir / _ACK_NAME
    if target.is_symlink():
        raise CookieTransferError("Cookie transfer acknowledgment must not be a symbolic link")
    with tempfile.NamedTemporaryFile(
        dir=profile_dir, prefix=".cookie-ack-", delete=False
    ) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(json.dumps(nonce).encode())
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
            temporary.chmod(0o600)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)


def take_pending_cookies(profile_dir: Path) -> dict | None:
    """Read the durable handoff offline; only delete an acknowledged transfer.

    A process dying before browser startup or application leaves the pending
    payload untouched. The next launch can safely retry it.
    """
    database = profile_dir / "cookies.sqlite"
    payload = _read_pending(database, honor_ack=False)
    if payload is not None and _read_ack(profile_dir / _ACK_NAME) == payload["nonce"]:
        _set_pending(database, None)
        (profile_dir / _ACK_NAME).unlink(missing_ok=True)
        return None
    return payload


def discard_pending_ack(profile_dir: Path, nonce: str) -> None:
    """An unexposed failed launch must leave its durable handoff retryable."""
    path = profile_dir / _ACK_NAME
    if _read_ack(path) == nonce:
        path.unlink(missing_ok=True)


async def apply_pending_cookies(browser: Any, pending: dict | None, profile_dir: Path) -> None:
    """Apply through browser APIs, then acknowledge outside live Firefox SQLite."""
    if pending is None:
        return
    batch = parse_cookies(json.dumps(pending["cookies"]), "json")
    try:
        if pending.get("replace"):
            await browser.clear_cookies()
        if batch.cookies:
            await browser.add_cookies(cast(Any, batch.cookies))
        await run_file_operation(_write_ack, profile_dir, pending["nonce"])
    except Exception:
        raise CookieTransferError(
            "Could not apply pending cookies; the transfer is retained for retry"
        ) from None


async def _stage_store(
    profile, directory: Path, source: Path, batch: CookieBatch | None, mode: str
):
    database = directory / "cookies.sqlite"
    await run_file_operation(_copy_store, source, database)
    pending = await run_file_operation(_read_pending, database, source.parent / _ACK_NAME)
    options = profile.to_camoufox_launch_options()
    options.pop("proxy", None)
    options.update(
        headless=True, user_data_dir=str(directory), geoip=False, exclude_addons=list(DefaultAddons)
    )
    options["config"] = {**(profile.fingerprint or {}), **options["config"]}
    from .browser_session import BrowserSession, _browser_launch_options, _resolve_process_id

    options = _browser_launch_options(options)
    camoufox = AsyncCamoufox(**options)
    session = BrowserSession(profile.id, camoufox)
    try:
        opened = await camoufox.start()
        session.process_id = _resolve_process_id(opened) or _resolve_process_id(camoufox)
        browser = cast(BrowserContext, opened)
        if pending:
            previous = parse_cookies(json.dumps(pending["cookies"]), "json").cookies
            if pending.get("replace"):
                await browser.clear_cookies()
            if previous:
                await browser.add_cookies(cast(Any, previous))
        if batch is not None:
            if mode == "replace":
                await browser.clear_cookies()
            if batch.cookies:
                await browser.add_cookies(cast(Any, batch.cookies))
        cookies = [dict(cookie) for cookie in await browser.cookies()]
        if batch is not None:
            actual = {_key(cookie): cookie for cookie in cookies}

            def accepted(cookie):
                saved = actual.get(_key(cookie))
                if saved is None:
                    return False
                if any(
                    saved.get(field) != cookie[field]
                    for field in ("value", "secure", "httpOnly", "sameSite")
                ):
                    return False
                expected, observed = cookie["expires"], saved.get("expires")
                return (
                    observed == -1
                    if expected == -1
                    else isinstance(observed, (int, float)) and abs(observed - expected) <= 1
                )

            if any(not accepted(cookie) for cookie in batch.cookies):
                raise CookieTransferError(
                    "The browser rejected a cookie; no profile cookies were changed"
                )
    except CookieTransferError:
        raise
    except Exception:
        raise CookieTransferError(
            "Could not prepare cookie storage. Install the browser and retry; no profile cookies were changed"
        ) from None
    finally:
        # The same owned/drained teardown as normal sessions prevents repeated
        # request cancellation from orphaning a staging browser or its driver.
        await session.terminate()
    if batch is not None:
        replace = mode == "replace" or bool(pending and pending.get("replace"))
        deferred = cookies if replace else [cookie for cookie in cookies if cookie["expires"] == -1]
        await run_file_operation(
            _set_pending,
            database,
            {"replace": replace, "cookies": deferred} if replace or deferred else None,
        )
    return cookies, database


class CookieTransfer:
    def __init__(self, manager, backups):
        self.manager = manager
        self.backups = backups

    async def preview(self, profile_id: str, text: str, format: CookieFormat) -> dict:
        if await self.manager.get_profile(profile_id) is None:
            raise CookieTransferError("Profile not found")
        return parse_cookies(text, format).preview()

    async def import_cookies(
        self,
        profile_id: str,
        text: str,
        format: CookieFormat,
        mode: Literal["merge", "replace"],
        row_version: int,
        confirm_replace: bool = False,
    ) -> dict:
        batch = parse_cookies(text, format)
        if mode not in ("merge", "replace"):
            raise CookieTransferError("Select merge or replace cookie import behavior")
        if mode == "replace" and not confirm_replace:
            raise CookieTransferError("Confirm replacement before importing cookies")
        ensure_browser_ready()
        holder = make_lease_holder()
        async with profile_operation(self.manager, profile_id, holder=holder) as profile:
            if profile.row_version != row_version:
                raise StaleWriteError(profile_id, row_version)
            backup = None
            if mode == "replace":
                try:
                    backup = await self.backups.create_locked_backup(
                        profile, holder, "before_cookie_replace"
                    )
                except (OSError, ValueError):
                    raise CookieTransferError(
                        "Could not create a profile backup; no cookies were changed"
                    ) from None
            source = profile_data_path(self.manager, profile) / "cookies.sqlite"
            with tempfile.TemporaryDirectory(
                prefix=".cookie-transfer-", dir=self.manager.profiles_dir
            ) as temporary:
                _, database = await _stage_store(profile, Path(temporary), source, batch, mode)
                with self.manager.storage.db._connection as connection:
                    changed = connection.execute(
                        "UPDATE profiles SET row_version=row_version+1, updated_at=? "
                        "WHERE id=? AND row_version=? AND deleted_at IS NULL AND locked_by=?",
                        (datetime.now().isoformat(), profile_id, row_version, holder),
                    )
                    if not changed.rowcount:
                        raise StaleWriteError(profile_id, row_version)
                await run_file_operation(_publish_store, database, source)
            return {
                "imported": len(batch.cookies),
                "skipped": batch.skipped,
                "session_count": batch.preview()["session_count"],
                "row_version": row_version + 1,
                "backup_id": backup["id"] if backup else None,
            }

    async def export_cookies(self, profile_id: str, format: CookieFormat, row_version: int) -> str:
        ensure_browser_ready()
        async with profile_operation(self.manager, profile_id) as profile:
            if profile.row_version != row_version:
                raise StaleWriteError(profile_id, row_version)
            source = profile_data_path(self.manager, profile) / "cookies.sqlite"
            with tempfile.TemporaryDirectory(
                prefix=".cookie-export-", dir=self.manager.profiles_dir
            ) as temporary:
                cookies, _ = await _stage_store(profile, Path(temporary), source, None, "merge")
            current = await self.manager.get_profile(profile_id)
            if current is None or current.row_version != row_version:
                raise StaleWriteError(profile_id, row_version)
            return serialize_cookies(cookies, format)
