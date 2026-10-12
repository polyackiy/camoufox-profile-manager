"""Reusable named proxy templates with additive, closed-profile assignment.

Metadata reads never select or decrypt passwords. Secret configuration is read
only for a deliberate check, assignment or duplicate import comparison. Profiles
receive independent snapshots; later inventory changes cannot affect them.
"""

from __future__ import annotations

import builtins
import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from camoufox_pm.config import get_settings

from . import proxy_check
from .crypto import decrypt, encrypt
from .database import StaleWriteError, _serialize_proxy
from .leases import ProfileLocked, make_lease_holder
from .models import BrowserSettings, ProxyConfig, ProxyType, generate_short_id
from .profile_operations import profile_operation
from .proxy_import import normalize_proxy_server

_PUBLIC_COLUMNS = (
    "id, name, type, server, username, country, row_version, last_check, "
    "created_at, updated_at, (password IS NOT NULL AND password != '') AS has_password"
)
_CONFIG_FIELDS = frozenset({"type", "server", "username", "password", "country"})


class ProxyNotFound(ValueError):
    """The named template no longer exists."""


class ProxyCredentialsUnavailable(RuntimeError):
    """Credentials must never silently pass through as encrypted text."""


def _stored_password(password: str | None) -> str | None:
    if not password:
        return None
    if not get_settings().secret_key:
        raise ProxyCredentialsUnavailable(
            "An encryption key is required to save proxy passwords. "
            "Restore secret.key or configure CPM_SECRET_KEY."
        )
    try:
        return encrypt(password)
    except (ValueError, TypeError):
        raise ProxyCredentialsUnavailable("The configured encryption key is invalid.") from None


def _clear_password(password: str | None) -> str | None:
    if not password:
        return None
    try:
        value = decrypt(password)
    except (ValueError, TypeError):
        raise ProxyCredentialsUnavailable("The configured encryption key is invalid.") from None
    if password.startswith("enc:") and value == password:
        raise ProxyCredentialsUnavailable(
            "Proxy credentials could not be decrypted. Restore the original "
            "secret.key or CPM_SECRET_KEY before checking or assigning this proxy."
        )
    return value


def _public(row) -> dict:
    result = dict(row)
    result["has_password"] = bool(result["has_password"])
    result["last_check"] = json.loads(result["last_check"]) if result["last_check"] else None
    return result


def _sanitize_check(record, config: ProxyConfig):
    """Do not reflect proxy credentials reported inside a transport failure."""
    secrets = [value for value in (config.password, config.username) if value]

    def clean(text):
        if text is None:
            return None
        for secret in secrets:
            for form in {secret, quote(secret, safe="")}:
                text = text.replace(form, "[redacted]")
        return text

    record.error = clean(record.error)
    for finding in record.findings:
        finding.message = clean(finding.message)
    return record


class ProxyInventory:
    def __init__(self, manager):
        self.manager = manager
        self.storage = manager.storage
        self.connection = self.storage.db._connection

    def _get(self, proxy_id: str):
        row = self.connection.execute(
            f"SELECT {_PUBLIC_COLUMNS} FROM proxy_inventory WHERE id = ?", (proxy_id,)
        ).fetchone()
        if row is None:
            raise ProxyNotFound("Proxy not found")
        return _public(row)

    def _secret_config(self, proxy_id: str, expected_version: int | None = None) -> ProxyConfig:
        row = self.connection.execute(
            "SELECT type, server, username, password, country, row_version "
            "FROM proxy_inventory WHERE id = ?",
            (proxy_id,),
        ).fetchone()
        if row is None:
            raise ProxyNotFound("Proxy not found")
        if expected_version is not None and row["row_version"] != expected_version:
            raise StaleWriteError(proxy_id, expected_version)
        return ProxyConfig(
            type=row["type"],
            server=row["server"],
            username=row["username"],
            password=_clear_password(row["password"]),
            country=row["country"],
        )

    async def list(self) -> builtins.list[dict]:
        rows = self.connection.execute(
            f"SELECT {_PUBLIC_COLUMNS} FROM proxy_inventory ORDER BY created_at DESC, id"
        ).fetchall()
        return [_public(row) for row in rows]

    async def create(self, values: dict) -> dict:
        proxy_id = generate_short_id()
        now = datetime.now(timezone.utc).isoformat()
        config = ProxyConfig(
            **{key: value for key, value in values.items() if key in _CONFIG_FIELDS}
        )
        config.server = normalize_proxy_server(config.server)
        if config.password and not config.username:
            raise ValueError("A proxy password requires a username")
        password = _stored_password(config.password)
        with self.connection:
            self.connection.execute(
                "INSERT INTO proxy_inventory "
                "(id,name,type,server,username,password,country,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    proxy_id,
                    values["name"],
                    config.type.value,
                    config.server,
                    config.username,
                    password,
                    config.country,
                    now,
                    now,
                ),
            )
        return self._get(proxy_id)

    async def update(self, proxy_id: str, changes: dict, expected_version: int) -> dict:
        current = self._get(proxy_id)
        if current["row_version"] != expected_version:
            raise StaleWriteError(proxy_id, expected_version)
        if "server" in changes:
            changes = {**changes, "server": normalize_proxy_server(changes["server"])}
        username = changes.get("username", current["username"])
        has_password = (
            bool(changes["password"]) if "password" in changes else current["has_password"]
        )
        if has_password and not username:
            raise ValueError("A proxy password requires a username")
        columns = []
        values = []
        for field in ("name", "type", "server", "username", "password", "country"):
            if field not in changes:
                continue
            value = changes[field]
            if field == "password":
                value = _stored_password(value)
            elif isinstance(value, ProxyType):
                value = value.value
            columns.append(f"{field} = ?")
            values.append(value)
        if not columns:
            return current
        if _CONFIG_FIELDS.intersection(changes):
            columns.append("last_check = NULL")
        columns.extend(["row_version = row_version + 1", "updated_at = ?"])
        values.extend([datetime.now(timezone.utc).isoformat(), proxy_id, expected_version])
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE proxy_inventory SET "
                + ", ".join(columns)
                + " WHERE id = ? AND row_version = ?",
                values,
            )
            if not cursor.rowcount:
                raise StaleWriteError(proxy_id, expected_version)
        return self._get(proxy_id)

    async def delete(self, proxy_id: str, expected_version: int) -> bool:
        self._get(proxy_id)
        with self.connection:
            cursor = self.connection.execute(
                "DELETE FROM proxy_inventory WHERE id = ? AND row_version = ?",
                (proxy_id, expected_version),
            )
            if not cursor.rowcount:
                raise StaleWriteError(proxy_id, expected_version)
        return True

    async def check(self, proxy_id: str) -> dict:
        before = self._get(proxy_id)
        config = self._secret_config(proxy_id, before["row_version"])
        started = datetime.now(timezone.utc)
        try:
            result = await proxy_check.check(config, BrowserSettings())
            record = _sanitize_check(proxy_check.record(result, checked_at=started), config)
        except Exception:
            # Transport/library exceptions may embed an authenticated URL.
            record = proxy_check.record(
                proxy_check.ProxyCheckResult(reachable=False, error="Proxy health check failed."),
                checked_at=started,
            )
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE proxy_inventory SET last_check = ? WHERE id = ? AND row_version = ? "
                "AND (last_check IS NULL OR json_extract(last_check, '$.checked_at') <= ?)",
                (record.model_dump_json(), proxy_id, before["row_version"], started.isoformat()),
            )
            if not cursor.rowcount:
                raise StaleWriteError(proxy_id, before["row_version"])
        return self._get(proxy_id)

    async def assign(
        self, proxy_id: str, profiles: builtins.list[dict], proxy_row_version: int
    ) -> dict:
        if not 1 <= len(profiles) <= 100:
            raise ValueError("Select between 1 and 100 profiles")
        config = self._secret_config(proxy_id, proxy_row_version)
        result: dict[str, builtins.list[Any]] = {"assigned": [], "skipped": []}
        for target in profiles:
            profile_id = target["id"]
            stored = await self.storage.get_profile(profile_id, include_deleted=True)
            if stored is None or stored.deleted_at:
                result["skipped"].append(
                    {
                        "id": profile_id,
                        "reason": "Profile is in Trash" if stored else "Profile not found",
                    }
                )
                continue
            try:
                operation_holder = make_lease_holder()
                async with profile_operation(self.manager, profile_id, holder=operation_holder):
                    # One targeted, version-checked statement guards both the
                    # requested template and profile. Other profile fields stay
                    # untouched; a copied config never links back to inventory.
                    with self.connection:
                        cursor = self.connection.execute(
                            "UPDATE profiles SET proxy_config = ?, proxy_check = NULL, "
                            "row_version = row_version + 1, updated_at = ? "
                            "WHERE id = ? AND row_version = ? AND deleted_at IS NULL "
                            "AND locked_by = ? AND EXISTS "
                            "(SELECT 1 FROM proxy_inventory WHERE id = ? AND row_version = ?)",
                            (
                                json.dumps(_serialize_proxy(config)),
                                datetime.now().isoformat(),
                                profile_id,
                                target["row_version"],
                                operation_holder,
                                proxy_id,
                                proxy_row_version,
                            ),
                        )
                        if not cursor.rowcount:
                            result["skipped"].append(
                                {
                                    "id": profile_id,
                                    "reason": "Profile or proxy changed; reload before assigning",
                                }
                            )
                        else:
                            result["assigned"].append(profile_id)
            except ProfileLocked:
                result["skipped"].append(
                    {"id": profile_id, "reason": "Profile is running or leased"}
                )
            except ValueError:
                result["skipped"].append(
                    {"id": profile_id, "reason": "Profile not found or in Trash"}
                )
        return result

    async def import_configs(self, configs: builtins.list[tuple[int, ProxyConfig]]) -> dict:
        """Parser validates the whole batch before this safe storage phase begins.

        Country is display metadata, so the same connection imported after its
        country was labeled still counts as a duplicate. A short write
        reservation serializes duplicate detection and insert across processes.
        """
        if not 1 <= len(configs) <= 100:
            raise ValueError("Import between 1 and 100 proxies")
        result: dict[str, builtins.list[Any]] = {"created": [], "skipped": []}
        for line, config in configs:
            try:
                with self.connection:
                    self.connection.execute("BEGIN IMMEDIATE")
                    candidates = self.connection.execute(
                        "SELECT id FROM proxy_inventory WHERE type = ? AND server = ? "
                        "AND username IS ?",
                        (config.type.value, config.server, config.username),
                    ).fetchall()
                    duplicate = any(
                        self._secret_config(row["id"]).password == config.password
                        for row in candidates
                    )
                    if duplicate:
                        result["skipped"].append({"line": line, "reason": "Proxy already exists"})
                        continue
                    result["created"].append(
                        await self.create({**config.model_dump(), "name": config.server})
                    )
            except ProxyCredentialsUnavailable:
                result["skipped"].append(
                    {
                        "line": line,
                        "reason": "Proxy credentials require the original encryption key",
                    }
                )
        return result
