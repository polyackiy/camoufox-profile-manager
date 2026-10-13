"""Numbered schema boundary around the legacy idempotent schema migration.

Version zero denotes pre-updater databases. Version one records the complete
current schema only after tables, column additions and indexes have succeeded.
New versions must add an explicit numbered migration before increasing CURRENT.
"""

import sqlite3

CURRENT_SCHEMA = 2


def check_schema(connection: sqlite3.Connection) -> int:
    current = connection.execute("PRAGMA user_version").fetchone()[0]
    if current > CURRENT_SCHEMA:
        raise ValueError(f"Database schema {current} requires a newer manager")
    if current < 0:
        raise ValueError("Invalid database schema")
    return current


def complete_migration(connection: sqlite3.Connection) -> None:
    current = check_schema(connection)
    if current == 0:
        # Check the essential baseline before publishing migration 1. Legacy
        # upgrades are idempotent and resumable when interrupted before here.
        required = {"profiles", "users", "sessions", "schedules", "proxy_inventory"}
        actual = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if not required <= actual:
            raise ValueError("Incomplete database migration")
        connection.execute("PRAGMA user_version = 1")
        connection.commit()

    if current < 2:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            columns = {r[1] for r in connection.execute("PRAGMA table_info(users)")}
            if "role" not in columns:
                connection.execute(
                    "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'administrator' CHECK(role IN ('administrator','operator','viewer'))"
                )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS profile_grants (username TEXT NOT NULL COLLATE NOCASE REFERENCES users(username) ON DELETE CASCADE, profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE, can_export INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(username,profile_id))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS audit_events (id INTEGER PRIMARY KEY AUTOINCREMENT, actor_kind TEXT NOT NULL, actor_username TEXT, action TEXT NOT NULL, object_id TEXT, result TEXT NOT NULL, created_at TEXT NOT NULL)"
            )
            columns = {r[1] for r in connection.execute("PRAGMA table_info(schedules)")}
            if "actor_kind" not in columns:
                connection.execute(
                    "ALTER TABLE schedules ADD COLUMN actor_kind TEXT NOT NULL DEFAULT 'system'"
                )
            if "actor_username" not in columns:
                connection.execute("ALTER TABLE schedules ADD COLUMN actor_username TEXT")
            if "actor_user_id" not in columns:
                connection.execute("ALTER TABLE schedules ADD COLUMN actor_user_id TEXT")
            connection.execute("PRAGMA user_version = 2")
