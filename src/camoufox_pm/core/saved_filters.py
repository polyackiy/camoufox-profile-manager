"""Instance-wide saved profile views: filter metadata, never browser identity."""

import json
import sqlite3
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .database import StaleWriteError
from .models import ProfileTags, generate_short_id


class ViewConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    search: str = Field("", max_length=255)
    status: Literal["all", "active", "inactive", "blocked", "maintenance"] = "all"
    group: str | None = Field(None, max_length=255)
    tags: ProfileTags = Field(default_factory=list)
    sort_key: Literal["name", "id", "group", "os", "status", "last_used"] = "name"
    sort_direction: Literal["asc", "desc"] = "asc"


class SavedFilter(BaseModel):
    id: str
    name: str
    config: ViewConfig
    row_version: int = 0
    created_at: datetime
    updated_at: datetime


def initialize_tables(connection: sqlite3.Connection) -> None:
    connection.execute("""
        CREATE TABLE IF NOT EXISTS saved_filters (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            config TEXT NOT NULL,
            row_version BIGINT NOT NULL DEFAULT 0,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL
        )
    """)


class SavedFilterStore:
    def __init__(self, storage):
        self.connection = storage.db._connection

    @staticmethod
    def _record(row) -> SavedFilter:
        return SavedFilter(
            id=row["id"],
            name=row["name"],
            config=json.loads(row["config"]),
            row_version=row["row_version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list(self) -> list[SavedFilter]:
        rows = self.connection.execute(
            "SELECT * FROM saved_filters ORDER BY name COLLATE NOCASE, id"
        ).fetchall()
        return [self._record(row) for row in rows]

    def get(self, filter_id: str) -> SavedFilter | None:
        row = self.connection.execute(
            "SELECT * FROM saved_filters WHERE id = ?", (filter_id,)
        ).fetchone()
        return self._record(row) if row else None

    def create(self, name: str, config: ViewConfig) -> SavedFilter:
        now = datetime.now().isoformat()
        filter_id = generate_short_id()
        with self.connection:
            self.connection.execute(
                "INSERT INTO saved_filters (id, name, config, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (filter_id, name.strip(), config.model_dump_json(), now, now),
            )
        result = self.get(filter_id)
        assert result is not None
        return result

    def update(
        self,
        filter_id: str,
        expected: int,
        *,
        name: str | None = None,
        config: ViewConfig | None = None,
    ) -> SavedFilter | None:
        current = self.get(filter_id)
        if current is None:
            return None
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE saved_filters SET name = ?, config = ?, updated_at = ?, row_version = row_version + 1 WHERE id = ? AND row_version = ?",
                (
                    name.strip() if name is not None else current.name,
                    (config or current.config).model_dump_json(),
                    datetime.now().isoformat(),
                    filter_id,
                    expected,
                ),
            )
            if not cursor.rowcount:
                raise StaleWriteError(filter_id, expected)
        return self.get(filter_id)

    def delete(self, filter_id: str, expected: int) -> bool:
        if self.get(filter_id) is None:
            return False
        with self.connection:
            cursor = self.connection.execute(
                "DELETE FROM saved_filters WHERE id = ? AND row_version = ?", (filter_id, expected)
            )
            if not cursor.rowcount:
                raise StaleWriteError(filter_id, expected)
        return True
