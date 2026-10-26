"""Bounded fresh-profile creation with atomic, credential-free retry receipts."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import datetime, timezone

from .database import StaleWriteError
from .profile_templates import ProfileTemplates
from .proxy_import import normalize_proxy_server
from .proxy_inventory import ProxyInventory, _stored_password


class BatchConflict(ValueError):
    pass


class BatchAccessLost(RuntimeError):
    pass


class _AlreadyCreated(RuntimeError):
    pass


def initialize_tables(connection):
    # Additive operation metadata: older managers neither execute this endpoint
    # nor enforce these receipts. No profile/sync safety invariant is changed.
    connection.execute("""CREATE TABLE IF NOT EXISTS template_creation_batches(
        id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, template_id TEXT NOT NULL,
        request_json TEXT NOT NULL, preview_json TEXT NOT NULL, created_at TEXT NOT NULL)""")
    connection.execute("""CREATE TABLE IF NOT EXISTS template_creation_items(
        batch_id TEXT NOT NULL REFERENCES template_creation_batches(id),
        position INTEGER NOT NULL, profile_id TEXT, error TEXT,
        PRIMARY KEY(batch_id, position))""")


def _name_key(name):
    return unicodedata.normalize("NFKC", name).casefold()


class TemplateBatches:
    def __init__(self, manager):
        self.manager = manager
        self.connection = manager.storage.db._connection
        self.templates = ProfileTemplates(manager)
        self.proxies = ProxyInventory(manager)

    def _batch(self, batch_id):
        row = self.connection.execute(
            "SELECT * FROM template_creation_batches WHERE id=?", (batch_id,)
        ).fetchone()
        if row is None:
            raise LookupError("Batch not found")
        return row

    def get(self, batch_id):
        batch = self._batch(batch_id)
        request = json.loads(batch["request_json"])
        items = []
        for row in self.connection.execute(
            "SELECT i.*, p.id AS present, p.deleted_at FROM template_creation_items i "
            "LEFT JOIN profiles p ON p.id=i.profile_id WHERE i.batch_id=? ORDER BY i.position",
            (batch_id,),
        ):
            status = (
                (
                    "in_trash"
                    if row["deleted_at"]
                    else "created"
                    if row["present"]
                    else "unavailable"
                )
                if row["profile_id"]
                else "failed"
                if row["error"]
                else "pending"
            )
            items.append(
                {
                    "position": row["position"],
                    "name": request["names"][row["position"]],
                    "profile_id": row["profile_id"],
                    "status": status,
                    "error": row["error"],
                }
            )
        return {
            "id": batch_id,
            "template_id": batch["template_id"],
            "created_at": batch["created_at"],
            "request": request,
            "preview": json.loads(batch["preview_json"]),
            "items": items,
        }

    def list(self):
        return [
            self.get(row[0])
            for row in self.connection.execute(
                "SELECT id FROM template_creation_batches ORDER BY created_at DESC, id LIMIT 20"
            )
        ]

    def preview(self, template_id, data):
        self._validate(template_id, data, list(range(len(data["names"]))))
        return {
            "names": data["names"],
            "proxy_mapping": [
                {
                    key: value
                    for key, value in self.proxies._get(ref["id"]).items()
                    if key in {"id", "name", "type", "server"}
                }
                for ref in data["proxy_refs"]
            ],
        }

    def _validate(self, template_id, data, positions):
        if not positions:
            return
        row = self.connection.execute(
            "SELECT row_version FROM profile_templates WHERE id=?", (template_id,)
        ).fetchone()
        if row is None:
            raise LookupError("Template not found")
        if row[0] != data["row_version"]:
            raise StaleWriteError(template_id, data["row_version"])
        if (
            data["group"] is not None
            and not self.connection.execute(
                "SELECT 1 FROM profile_groups WHERE id=?", (data["group"],)
            ).fetchone()
        ):
            raise ValueError("Selected group no longer exists")
        if not data["allow_duplicate_names"]:
            keys = [_name_key(name) for name in data["names"]]
            if len(set(keys)) != len(keys):
                raise ValueError(
                    "Names repeat within this batch; change the pattern or explicitly allow duplicates"
                )
            existing = {
                _name_key(row[0])
                for row in self.connection.execute(
                    "SELECT name FROM profiles WHERE deleted_at IS NULL"
                )
            }
            if any(keys[position] in existing for position in positions):
                raise ValueError(
                    "A profile name already exists; change the names or explicitly allow duplicates"
                )
        for position in positions:
            if data["proxy_refs"]:
                ref = data["proxy_refs"][position]
                config = self.proxies._secret_config(ref["id"], ref["row_version"])
                normalize_proxy_server(config.server)
                _stored_password(
                    config.password
                )  # Never copy an unencrypted password without a key.

    async def submit(self, template_id, data, *, authorize, on_created=None):
        await authorize()
        batch_id = data["batch_id"]
        encoded = json.dumps(data, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256((template_id + "\n" + encoded).encode()).hexdigest()
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            batch = self.connection.execute(
                "SELECT request_hash FROM template_creation_batches WHERE id=?", (batch_id,)
            ).fetchone()
            if batch is not None and batch[0] != digest:
                raise BatchConflict("This batch identifier belongs to a different preview")
            completed = {
                row[0]
                for row in self.connection.execute(
                    "SELECT position FROM template_creation_items WHERE batch_id=? AND profile_id IS NOT NULL",
                    (batch_id,),
                )
            }
            self._validate(
                template_id, data, [i for i in range(len(data["names"])) if i not in completed]
            )
            if batch is None:
                self.connection.execute(
                    "INSERT INTO template_creation_batches(id,request_hash,template_id,request_json,preview_json,created_at) VALUES(?,?,?,?,?,?)",
                    (
                        batch_id,
                        digest,
                        template_id,
                        encoded,
                        json.dumps(self.preview(template_id, data)),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                self.connection.executemany(
                    "INSERT INTO template_creation_items(batch_id,position) VALUES(?,?)",
                    [(batch_id, i) for i in range(len(data["names"]))],
                )

        for position, name in enumerate(data["names"]):
            receipt = self.connection.execute(
                "SELECT profile_id FROM template_creation_items WHERE batch_id=? AND position=?",
                (batch_id, position),
            ).fetchone()
            if receipt[0] is not None:
                continue
            await authorize()

            def publish(profile, position=position):
                receipt = self.connection.execute(
                    "SELECT profile_id FROM template_creation_items WHERE batch_id=? AND position=?",
                    (batch_id, position),
                ).fetchone()
                if receipt[0] is not None:
                    raise _AlreadyCreated
                self._validate(template_id, data, [position])
                if data["proxy_refs"]:
                    ref = data["proxy_refs"][position]
                    profile.proxy = self.proxies._secret_config(ref["id"], ref["row_version"])
                self.connection.execute(
                    "UPDATE template_creation_items SET profile_id=?,error=NULL WHERE batch_id=? AND position=?",
                    (profile.id, batch_id, position),
                )

            try:
                profile = await self.templates.create_profile(
                    template_id,
                    data["row_version"],
                    name,
                    data["group"],
                    before_publish=authorize,
                    on_publish=publish,
                )
                if on_created is not None:
                    await on_created(profile.id)
            except _AlreadyCreated:
                continue
            except BatchAccessLost:
                raise
            except Exception as exc:
                # The publication and its receipt commit together. Never change
                # a committed result if e.g. subsequent audit recording fails.
                with self.connection:
                    self.connection.execute(
                        "UPDATE template_creation_items SET error=? WHERE batch_id=? AND position=? AND profile_id IS NULL",
                        (
                            "The template or proxy changed. Create a new preview for profiles not created."
                            if isinstance(exc, StaleWriteError)
                            else "Creation failed. Check the template, proxy and storage before retrying.",
                            batch_id,
                            position,
                        ),
                    )
        return self.get(batch_id)
