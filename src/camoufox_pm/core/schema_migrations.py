"""Numbered schema boundary around the legacy idempotent schema migration.

Version zero denotes pre-updater databases. Version one records the complete
current schema only after tables, column additions and indexes have succeeded.
New versions must add an explicit numbered migration before increasing CURRENT.
"""

import sqlite3

CURRENT_SCHEMA = 1


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
