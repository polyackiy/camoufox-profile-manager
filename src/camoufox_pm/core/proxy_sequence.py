"""Explicit ordered proxy mapping, guarded by the existing assignment operation."""

from __future__ import annotations

import json
import sqlite3
from urllib.parse import urlsplit

from .database import StaleWriteError
from .leases import lease_expired
from .proxy_import import normalize_proxy_server
from .proxy_inventory import (
    ProxyCredentialsUnavailable,
    ProxyInventory,
    ProxyNotFound,
    _stored_password,
)


def connection_label(config: dict | None) -> str:
    if not config or not config.get("server"):
        return "Direct connection"
    try:
        server = config["server"]
        address = urlsplit(
            server if "://" in server else f"{config.get('type', 'http')}://{server}"
        )
        host = address.hostname
        if not host or not address.port:
            return "Proxy configured"
        if ":" in host:
            host = f"[{host}]"
        return f"{address.scheme}://{host}:{address.port}"
    except (ValueError, TypeError):
        return "Proxy configured"


class ProxySequence:
    def __init__(self, inventory: ProxyInventory):
        self.inventory = inventory
        self.connection = inventory.connection

    def validate(self, assignments: list[dict]) -> None:
        if not 1 <= len(assignments) <= 100:
            raise ValueError("Select between 1 and 100 profiles")
        for key in ("profile", "proxy"):
            if len({item[key]["id"] for item in assignments}) != len(assignments):
                raise ValueError("Select distinct profiles and saved proxies")
        # Validate the entire saved-proxy selection before the first write.
        # No credentials are retained in requests, previews or results.
        for item in assignments:
            ref = item["proxy"]
            config = self.inventory._secret_config(ref["id"], ref["row_version"])
            config.validate_authentication()
            normalize_proxy_server(config.server)
            _stored_password(config.password)

    async def preview(self, assignments: list[dict]) -> list[dict]:
        self.validate(assignments)
        result = []
        for item in assignments:
            target, ref = item["profile"], item["proxy"]
            proxy = self.inventory._get(ref["id"])
            row = self.connection.execute(
                "SELECT name,proxy_config,row_version,deleted_at,locked_by,lock_expires "
                "FROM profiles WHERE id=?",
                (target["id"],),
            ).fetchone()
            reason = None
            if row is None or row["deleted_at"]:
                reason = "Profile not found or in Trash"
            elif row["row_version"] != target["row_version"]:
                reason = "Profile or proxy changed; reload before assigning"
            elif self.inventory.manager.browser_sessions.is_live(target["id"]) or (
                row["locked_by"] and not lease_expired(row["lock_expires"])
            ):
                reason = "Profile is running or leased"
            config = json.loads(row["proxy_config"]) if row and row["proxy_config"] else None
            result.append(
                {
                    "profile_id": target["id"],
                    "profile_name": row["name"] if row else target["id"],
                    "current_connection": connection_label(config),
                    "proxy_id": ref["id"],
                    "proxy_name": proxy["name"],
                    "connection": connection_label(proxy),
                    "replaces_proxy": bool(config),
                    "reason": reason,
                }
            )
        return result

    async def assign(self, assignments: list[dict], *, authorize, on_assigned=None) -> dict:
        await authorize()
        self.validate(assignments)
        result: dict[str, list] = {"assigned": [], "skipped": []}
        for item in assignments:
            await authorize()
            target, ref = item["profile"], item["proxy"]
            try:
                # Original versions and explicit pairs never change after a
                # partial/uncertain response. Replays cannot overwrite newer rows.
                part = await self.inventory.assign(
                    ref["id"], [target], ref["row_version"], authorize=authorize
                )
            except (StaleWriteError, ProxyNotFound):
                part = {
                    "assigned": [],
                    "skipped": [
                        {
                            "id": target["id"],
                            "reason": "Profile or proxy changed; reload before assigning",
                        }
                    ],
                }
            except ProxyCredentialsUnavailable:
                part = {
                    "assigned": [],
                    "skipped": [
                        {
                            "id": target["id"],
                            "reason": "Proxy credentials require the original encryption key",
                        }
                    ],
                }
            except (OSError, sqlite3.Error):
                part = {
                    "assigned": [],
                    "skipped": [
                        {
                            "id": target["id"],
                            "reason": "Assignment could not be confirmed. Reload profiles before trying again.",
                        }
                    ],
                }
            result["assigned"].extend(part["assigned"])
            result["skipped"].extend(part["skipped"])
            if on_assigned is not None:
                for profile_id in part["assigned"]:
                    await on_assigned(profile_id)
        return result
