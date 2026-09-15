"""
SQLite storage layer for the Camoufox profile management system.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from loguru import logger
from pydantic import ValidationError

from .crypto import decrypt, encrypt
from .leases import lease_expired
from .models import (
    Profile,
    ProfileGroup,
    ProfileStatus,
    ProxyCheckRecord,
    ProxyConfig,
    Schedule,
    ScheduleRun,
    UsageStats,
)


def _serialize_proxy(proxy: ProxyConfig) -> dict:
    """Serialize a proxy for storage, encrypting the password."""
    data = proxy.model_dump()
    if data.get("password"):
        data["password"] = encrypt(data["password"])
    return data


def _deserialize_proxy(data: dict) -> ProxyConfig:
    """Rebuild a proxy from storage, decrypting the password."""
    if data.get("password"):
        data["password"] = decrypt(data["password"])
    return ProxyConfig(**data)


class StaleWriteError(Exception):
    """A save built on an older version of the row than the one stored.

    The optimistic half of this project's concurrency story; core/leases.py is
    the pessimistic half. A lease answers "who may *run* this profile"; this
    answers "who may *save* this row", which is a different question because a
    profile can be edited while nobody is running it.

    Raised when a version-checked update matched no rows: somebody saved first.
    The later edit is lost loudly instead of silently overwriting theirs.
    """

    def __init__(self, profile_id: str, expected_row_version: int | None = None):
        self.profile_id = profile_id
        self.expected_row_version = expected_row_version
        super().__init__(
            f"Profile {profile_id} was changed by someone else since it was read; "
            "reload it and apply the edit again"
        )


class DatabaseManager:
    """Async-friendly SQLite database manager."""

    def __init__(self, db_path: str = "data/profiles.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection: sqlite3.Connection = None  # type: ignore[assignment]
        logger.info(f"DatabaseManager initialized with database: {self.db_path}")

    async def initialize(self):
        """Initialize the database and create tables."""
        self._connection = sqlite3.connect(str(self.db_path), timeout=30.0)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")

        await self._create_tables()
        await self._migrate()
        await self._create_indexes()
        logger.info("Database initialized")

    async def _migrate(self):
        """Bring an existing database up to the current schema.

        Tables are created with ``CREATE TABLE IF NOT EXISTS``, so a database made
        by an older version keeps its original columns forever. Each entry here
        adds one column when it is missing; adding a column is safe to re-run and
        never touches existing rows.
        """
        added_columns = {
            "profiles": [
                ("fingerprint", "TEXT"),
                ("proxy_check", "TEXT"),
                # Lease columns (see core/leases.py). NULL means free, which is
                # what an existing row gets when the column is added — every
                # profile in an upgraded database starts unleased.
                ("locked_by", "TEXT"),
                ("lock_expires", "TIMESTAMP"),
                # Optimistic-concurrency counter. Existing rows start at 0, so
                # the first version-checked save of an upgraded database has a
                # version to match against.
                ("row_version", "BIGINT NOT NULL DEFAULT 0"),
            ],
        }
        for table, columns in added_columns.items():
            cursor = self._connection.execute(f"PRAGMA table_info({table})")
            existing = {row["name"] for row in cursor.fetchall()}
            for name, column_type in columns:
                if name in existing:
                    continue
                self._connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {column_type}")
                logger.info(f"Migrated {table}: added column {name}")
        self._connection.commit()

    async def _create_tables(self):
        """Create the database tables."""
        # Profiles table
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS profiles (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                group_id TEXT,
                status TEXT DEFAULT 'active',
                browser_settings TEXT NOT NULL,
                proxy_config TEXT,
                extensions TEXT,
                storage_path TEXT,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_used TIMESTAMP,
                fingerprint TEXT,
                proxy_check TEXT,
                locked_by TEXT,
                lock_expires TIMESTAMP,
                row_version BIGINT NOT NULL DEFAULT 0
            )
        """)

        # Profile groups table
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS profile_groups (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                profile_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Usage statistics table
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS usage_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id TEXT NOT NULL,
                action TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                duration INTEGER,
                success BOOLEAN DEFAULT 1,
                details TEXT
            )
        """)

        # User accounts for the web UI. Their mere existence turns login on, so a
        # database without rows here behaves exactly as before the table existed.
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Login sessions, keyed by the SHA-256 of the cookie token — the token
        # itself is never stored, so this table cannot be replayed if leaked.
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL
            )
        """)

        # Scheduled tasks. New tables need no _migrate entry: IF NOT EXISTS adds
        # them to an existing database without touching what is already there.
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS schedules (
                id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL,
                action TEXT NOT NULL,
                kind TEXT NOT NULL,
                interval_minutes INTEGER,
                at_time TEXT,
                days TEXT,
                run_minutes INTEGER,
                enabled BOOLEAN DEFAULT 1,
                next_run_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS schedule_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                schedule_id TEXT NOT NULL,
                started_at TIMESTAMP NOT NULL,
                finished_at TIMESTAMP,
                outcome TEXT NOT NULL,
                message TEXT
            )
        """)

        self._connection.commit()

    async def _create_indexes(self):
        """Create indexes to optimize queries."""
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_profiles_group ON profiles(group_id)",
            "CREATE INDEX IF NOT EXISTS idx_profiles_status ON profiles(status)",
            "CREATE INDEX IF NOT EXISTS idx_profiles_created ON profiles(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_usage_stats_profile ON usage_stats(profile_id)",
            "CREATE INDEX IF NOT EXISTS idx_usage_stats_timestamp ON usage_stats(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_schedules_profile ON schedules(profile_id)",
            "CREATE INDEX IF NOT EXISTS idx_schedule_runs_schedule ON schedule_runs(schedule_id)",
        ]

        for index_sql in indexes:
            self._connection.execute(index_sql)

        self._connection.commit()

    # --- Profiles ---

    def _profile_columns(self, profile: Profile) -> tuple:
        """The column values a save owns, in the order both writers use.

        Shared so the upsert and the version-checked update cannot drift apart:
        a column added to one and forgotten in the other would be written on a
        create and silently ignored on an edit.
        """
        return (
            profile.id,
            profile.name,
            profile.group,
            profile.status.value if hasattr(profile.status, "value") else profile.status,
            json.dumps(profile.browser_settings.model_dump()),
            json.dumps(_serialize_proxy(profile.proxy)) if profile.proxy else None,
            json.dumps(profile.extensions),
            profile.storage_path,
            profile.notes,
            profile.created_at.isoformat(),
            profile.updated_at.isoformat(),
            profile.last_used.isoformat() if profile.last_used else None,
            json.dumps(profile.fingerprint) if profile.fingerprint else None,
            profile.proxy_check.model_dump_json() if profile.proxy_check else None,
        )

    def _upsert_profile(self, profile: Profile) -> None:
        """Insert the row, or overwrite the columns a save owns.

        An upsert rather than ``INSERT OR REPLACE``, which deletes the row and
        writes a new one: that would drop the lease and version columns, so
        renaming a profile would quietly unlock a browser another instance is
        running. ``DO UPDATE`` names only the columns a save owns, and the ones
        it does not name keep their values.
        """
        self._connection.execute(
            """
            INSERT INTO profiles (
                id, name, group_id, status, browser_settings, proxy_config,
                extensions, storage_path, notes, created_at, updated_at, last_used,
                fingerprint, proxy_check
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                group_id = excluded.group_id,
                status = excluded.status,
                browser_settings = excluded.browser_settings,
                proxy_config = excluded.proxy_config,
                extensions = excluded.extensions,
                storage_path = excluded.storage_path,
                notes = excluded.notes,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at,
                last_used = excluded.last_used,
                fingerprint = excluded.fingerprint,
                proxy_check = excluded.proxy_check
        """,
            self._profile_columns(profile),
        )

    async def save_profile(self, profile: Profile, expected_row_version: int | None = None):
        """Save a profile.

        With ``expected_row_version`` the write lands only while the stored
        ``row_version`` still matches what the caller read, and bumps it;
        matching no rows raises ``StaleWriteError`` rather than overwriting
        whoever got there first. That is what makes editing one profile from
        two places safe. Without a version the row is written as before.

        Either way the lease columns survive untouched: a save must never break
        or take another instance's lease.
        """
        profile.updated_at = datetime.now()
        if expected_row_version is None:
            self._upsert_profile(profile)
        else:
            self._save_profile_if_unchanged(profile, expected_row_version)
        self._connection.commit()
        logger.debug(f"Profile {profile.name} saved")

    def _save_profile_if_unchanged(self, profile: Profile, expected_row_version: int) -> None:
        """One guarded statement: update only while the version still matches.

        Never creates a row. There is nothing to guard on a row that does not
        exist yet, and silently inserting one would turn "somebody deleted this
        profile while you were editing it" into a resurrection.
        """
        cursor = self._connection.execute(
            """
            UPDATE profiles SET
                name = ?, group_id = ?, status = ?, browser_settings = ?,
                proxy_config = ?, extensions = ?, storage_path = ?, notes = ?,
                created_at = ?, updated_at = ?, last_used = ?, fingerprint = ?,
                proxy_check = ?, row_version = row_version + 1
            WHERE id = ? AND row_version = ?
            """,
            (*self._profile_columns(profile)[1:], profile.id, expected_row_version),
        )
        if cursor.rowcount == 0:
            raise StaleWriteError(profile.id, expected_row_version)
        # Keep the caller's copy in step with the row it just wrote: the next
        # edit reads its row_version, and it must be the one this write produced.
        profile.row_version = expected_row_version + 1

    async def get_profile(self, profile_id: str) -> Profile | None:
        """Get a profile by ID."""
        cursor = self._connection.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,))
        row = cursor.fetchone()

        if row:
            return self._row_to_profile(row)
        return None

    async def update_profile(self, profile: Profile, expected_row_version: int | None = None):
        """Update a profile, optionally guarded against a concurrent save."""
        await self.save_profile(profile, expected_row_version)
        logger.debug(f"Profile {profile.name} updated")

    async def set_proxy_check(self, profile_id: str, record: ProxyCheckRecord | None) -> None:
        """Write only the proxy check, leaving every other column alone.

        A check takes seconds — up to thirty against a proxy that never answers —
        and `save_profile` replaces the whole row. Writing back a Profile read
        before that wait would revert anything edited during it, which is the
        hazard already noted in profile_manager.launch_browser. This also keeps a
        check from touching `updated_at`: asking a proxy a question is not an
        edit, and a bulk check should not make a selection look modified.
        """
        self._connection.execute(
            "UPDATE profiles SET proxy_check = ? WHERE id = ?",
            (record.model_dump_json() if record else None, profile_id),
        )
        self._connection.commit()

    async def set_launch_pin(
        self, profile_id: str, fingerprint: dict | None, last_used: datetime
    ) -> None:
        """Write only what a launch owns: the pinned machine and the timestamp.

        A targeted write for the same reason as ``set_proxy_check``. A launch
        resolves a fingerprint, which can fetch the uBlock addon over the
        network on a fresh install, and writing a whole Profile back after that
        would revert anything edited while it ran. Naming the two columns a
        launch actually owns removes the hazard rather than guarding against it,
        and leaves the row's version alone: starting a browser is not an edit of
        the profile, so it must not make a concurrent editor's save go stale.
        """
        self._connection.execute(
            "UPDATE profiles SET fingerprint = ?, last_used = ?, updated_at = ? WHERE id = ?",
            (
                json.dumps(fingerprint) if fingerprint else None,
                last_used.isoformat(),
                last_used.isoformat(),
                profile_id,
            ),
        )
        self._connection.commit()

    async def delete_profile(self, profile_id: str) -> bool:
        """Delete a profile, and the schedules that would otherwise fire against it."""
        self._connection.execute(
            "DELETE FROM schedule_runs WHERE schedule_id IN "
            "(SELECT id FROM schedules WHERE profile_id = ?)",
            (profile_id,),
        )
        self._connection.execute("DELETE FROM schedules WHERE profile_id = ?", (profile_id,))
        cursor = self._connection.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        self._connection.commit()
        deleted = cursor.rowcount > 0

        if deleted:
            logger.debug(f"Profile {profile_id} deleted")

        return deleted

    async def list_profiles(
        self, filters: dict | None = None, limit: int | None = None, offset: int = 0
    ) -> list[Profile]:
        """List profiles with optional filtering."""
        query = "SELECT * FROM profiles WHERE 1=1"
        params = []

        if filters:
            if "group" in filters:
                query += " AND group_id = ?"
                params.append(filters["group"])
            if "status" in filters:
                query += " AND status = ?"
                params.append(filters["status"])
            if "name_like" in filters:
                query += " AND name LIKE ?"
                params.append(f"%{filters['name_like']}%")

        query += " ORDER BY created_at DESC"

        # SQLite will not take an OFFSET without a LIMIT, so an offset on its own
        # used to be dropped and the caller silently got the first page back.
        if limit or offset:
            query += " LIMIT ?"
            params.append(limit if limit else -1)
        if offset:
            query += " OFFSET ?"
            params.append(offset)

        cursor = self._connection.execute(query, params)
        rows = cursor.fetchall()

        return [self._row_to_profile(row) for row in rows]

    async def count_profiles(self, filters: dict | None = None) -> int:
        """Count profiles."""
        query = "SELECT COUNT(*) FROM profiles WHERE 1=1"
        params = []

        if filters:
            if "group" in filters:
                query += " AND group_id = ?"
                params.append(filters["group"])
            if "status" in filters:
                query += " AND status = ?"
                params.append(filters["status"])

        cursor = self._connection.execute(query, params)
        return cursor.fetchone()[0]

    # --- Profile groups ---

    async def save_profile_group(self, group: ProfileGroup):
        """Save a profile group."""
        self._connection.execute(
            """
            INSERT OR REPLACE INTO profile_groups (
                id, name, description, profile_count, created_at
            ) VALUES (?, ?, ?, ?, ?)
        """,
            (
                group.id,
                group.name,
                group.description,
                group.profile_count,
                group.created_at.isoformat(),
            ),
        )
        self._connection.commit()
        logger.debug(f"Group {group.name} saved")

    async def list_profile_groups(self) -> list[ProfileGroup]:
        """List all groups."""
        cursor = self._connection.execute("""
            SELECT pg.*, COUNT(p.id) as actual_count
            FROM profile_groups pg
            LEFT JOIN profiles p ON pg.id = p.group_id
            GROUP BY pg.id
            ORDER BY pg.created_at DESC
        """)
        rows = cursor.fetchall()

        groups = []
        for row in rows:
            group = ProfileGroup(
                id=row["id"],
                name=row["name"],
                description=row["description"],
                profile_count=row["actual_count"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            groups.append(group)

        return groups

    async def delete_profile_group(self, group_id: str) -> bool:
        """Delete a profile group."""
        # Ungroup the profiles that belonged to this group
        self._connection.execute(
            "UPDATE profiles SET group_id = NULL WHERE group_id = ?", (group_id,)
        )

        # Delete the group
        cursor = self._connection.execute("DELETE FROM profile_groups WHERE id = ?", (group_id,))
        self._connection.commit()

        return cursor.rowcount > 0

    # --- Statistics ---

    async def log_usage(self, usage_stats: UsageStats):
        """Record a usage statistic."""
        self._connection.execute(
            """
            INSERT INTO usage_stats (
                profile_id, action, timestamp, duration, success, details
            ) VALUES (?, ?, ?, ?, ?, ?)
        """,
            (
                usage_stats.profile_id,
                usage_stats.action,
                usage_stats.timestamp.isoformat(),
                usage_stats.duration,
                usage_stats.success,
                json.dumps(usage_stats.details) if usage_stats.details else None,
            ),
        )
        self._connection.commit()

    async def get_profile_usage_stats(self, profile_id: str, limit: int = 100) -> list[UsageStats]:
        """Get usage statistics for a profile."""
        cursor = self._connection.execute(
            """
            SELECT * FROM usage_stats
            WHERE profile_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """,
            (profile_id, limit),
        )

        rows = cursor.fetchall()
        stats = []

        for row in rows:
            stat = UsageStats(
                id=row["id"],
                profile_id=row["profile_id"],
                action=row["action"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                duration=row["duration"],
                success=bool(row["success"]),
                details=json.loads(row["details"]) if row["details"] else None,
            )
            stats.append(stat)

        return stats

    # --- Users and sessions ---

    async def create_user(self, user_id: str, username: str, password_hash: str) -> None:
        """Create a user; raises ``ValueError`` when the username is taken."""
        try:
            self._connection.execute(
                "INSERT INTO users (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (user_id, username, password_hash, datetime.now().isoformat()),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"User {username!r} already exists") from exc
        self._connection.commit()
        # Deliberately logs the name only, never the hash.
        logger.info(f"User {username} created")

    async def get_user_by_username(self, username: str) -> dict | None:
        cursor = self._connection.execute(
            "SELECT id, username, password_hash FROM users WHERE username = ?", (username,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    async def update_user_password(self, username: str, password_hash: str) -> bool:
        cursor = self._connection.execute(
            "UPDATE users SET password_hash = ? WHERE username = ?", (password_hash, username)
        )
        self._connection.commit()
        return cursor.rowcount > 0

    async def delete_user(self, username: str) -> bool:
        """Delete a user; the foreign key cascades their open sessions away."""
        cursor = self._connection.execute("DELETE FROM users WHERE username = ?", (username,))
        self._connection.commit()
        return cursor.rowcount > 0

    async def count_users(self) -> int:
        cursor = self._connection.execute("SELECT COUNT(*) FROM users")
        return cursor.fetchone()[0]

    async def list_users(self) -> list[dict]:
        """Usernames and creation times only — hashes stay in the table."""
        cursor = self._connection.execute(
            "SELECT username, created_at FROM users ORDER BY created_at"
        )
        return [dict(row) for row in cursor.fetchall()]

    async def create_session(self, token_hash: str, user_id: str, expires_at: datetime) -> None:
        self._connection.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token_hash, user_id, datetime.now().isoformat(), expires_at.isoformat()),
        )
        self._connection.commit()

    async def get_session(self, token_hash: str) -> dict | None:
        cursor = self._connection.execute(
            """
            SELECT s.token_hash, s.user_id, s.expires_at, u.username
            FROM sessions s JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ?
            """,
            (token_hash,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    async def delete_session(self, token_hash: str) -> bool:
        cursor = self._connection.execute(
            "DELETE FROM sessions WHERE token_hash = ?", (token_hash,)
        )
        self._connection.commit()
        return cursor.rowcount > 0

    async def delete_expired_sessions(self) -> int:
        cursor = self._connection.execute(
            "DELETE FROM sessions WHERE expires_at <= ?", (datetime.now().isoformat(),)
        )
        self._connection.commit()
        return cursor.rowcount

    # --- Schedules ---

    async def save_schedule(self, schedule: Schedule):
        """Insert or replace a schedule."""
        self._connection.execute(
            """
            INSERT OR REPLACE INTO schedules (
                id, profile_id, action, kind, interval_minutes, at_time, days,
                run_minutes, enabled, next_run_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                schedule.id,
                schedule.profile_id,
                schedule.action,
                schedule.kind,
                schedule.interval_minutes,
                schedule.at_time,
                json.dumps(schedule.days) if schedule.days else None,
                schedule.run_minutes,
                schedule.enabled,
                schedule.next_run_at.isoformat() if schedule.next_run_at else None,
                schedule.created_at.isoformat(),
                schedule.updated_at.isoformat(),
            ),
        )
        self._connection.commit()

    async def get_schedule(self, schedule_id: str) -> Schedule | None:
        """Get a schedule by ID."""
        cursor = self._connection.execute("SELECT * FROM schedules WHERE id = ?", (schedule_id,))
        row = cursor.fetchone()
        return self._row_to_schedule(row) if row else None

    async def list_schedules(self, profile_id: str | None = None) -> list[Schedule]:
        """List schedules, oldest first so the UI order is stable."""
        if profile_id:
            cursor = self._connection.execute(
                "SELECT * FROM schedules WHERE profile_id = ? ORDER BY created_at",
                (profile_id,),
            )
        else:
            cursor = self._connection.execute("SELECT * FROM schedules ORDER BY created_at")
        return [self._row_to_schedule(row) for row in cursor.fetchall()]

    async def delete_schedule(self, schedule_id: str) -> bool:
        """Delete a schedule and its run history."""
        self._connection.execute("DELETE FROM schedule_runs WHERE schedule_id = ?", (schedule_id,))
        cursor = self._connection.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
        self._connection.commit()
        return cursor.rowcount > 0

    async def log_schedule_run(self, run: ScheduleRun, keep: int = 20) -> ScheduleRun:
        """Record one firing and prune the history to the newest ``keep`` rows.

        Bounded per schedule rather than by age: an every-five-minutes schedule
        would otherwise write hundreds of rows a day into a database that also
        holds the profiles.
        """
        cursor = self._connection.execute(
            """
            INSERT INTO schedule_runs (schedule_id, started_at, finished_at, outcome, message)
            VALUES (?, ?, ?, ?, ?)
        """,
            (
                run.schedule_id,
                run.started_at.isoformat(),
                run.finished_at.isoformat() if run.finished_at else None,
                run.outcome,
                run.message,
            ),
        )
        run.id = cursor.lastrowid
        self._connection.execute(
            """
            DELETE FROM schedule_runs WHERE schedule_id = ? AND id NOT IN (
                SELECT id FROM schedule_runs WHERE schedule_id = ? ORDER BY id DESC LIMIT ?
            )
        """,
            (run.schedule_id, run.schedule_id, keep),
        )
        self._connection.commit()
        return run

    async def list_schedule_runs(self, schedule_id: str, limit: int = 20) -> list[ScheduleRun]:
        """Get the newest runs of a schedule, newest first."""
        cursor = self._connection.execute(
            "SELECT * FROM schedule_runs WHERE schedule_id = ? ORDER BY id DESC LIMIT ?",
            (schedule_id, limit),
        )
        return [
            ScheduleRun(
                id=row["id"],
                schedule_id=row["schedule_id"],
                started_at=datetime.fromisoformat(row["started_at"]),
                finished_at=(
                    datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None
                ),
                outcome=row["outcome"],
                message=row["message"],
            )
            for row in cursor.fetchall()
        ]

    # --- Leases ---

    # See core/leases.py for what a lease is and why it exists. Every method
    # here is a single statement whose WHERE clause carries the decision, so
    # SQLite's write lock — not the application — picks the winner when two
    # processes ask at once.

    async def acquire_lease(self, profile_id: str, holder: str, ttl_seconds: int) -> bool:
        """Take the lease on a profile, if it is free. Never blocks.

        The three arms of the guard are the three ways a lease may be taken:
        nobody holds it, the holder's TTL ran out (their machine is gone), or
        we already hold it. That last arm makes re-acquisition idempotent — a
        process must be able to pick its own lease back up instead of waiting
        out a TTL it set itself.

        Returns ``False`` without touching the row when someone else holds an
        unexpired lease; callers turn that into ``ProfileLocked``.
        """
        cursor = self._connection.execute(
            """
            UPDATE profiles
               SET locked_by = ?,
                   lock_expires = datetime('now', '+' || ? || ' seconds')
             WHERE id = ?
               AND (locked_by IS NULL
                    OR locked_by = ?
                    OR julianday(lock_expires) < julianday('now'))
            """,
            (holder, ttl_seconds, profile_id, holder),
        )
        # Without the commit the lease lives inside this connection's open
        # transaction, where no other process can see it — the opposite of
        # what it is for.
        self._connection.commit()
        return cursor.rowcount > 0

    async def renew_lease(self, profile_ids: list[str], holder: str, ttl_seconds: int) -> int:
        """Push out the expiry of the leases we still hold; returns how many.

        A profile missing from the count has been taken over or has expired.
        The caller treats that as a lost lease rather than trying to win it
        back: something else is already driving that identity.
        """
        if not profile_ids:
            return 0
        placeholders = ",".join("?" for _ in profile_ids)
        cursor = self._connection.execute(
            f"""
            UPDATE profiles
               SET lock_expires = datetime('now', '+' || ? || ' seconds')
             WHERE id IN ({placeholders}) AND locked_by = ?
            """,
            (ttl_seconds, *profile_ids, holder),
        )
        self._connection.commit()
        return cursor.rowcount

    async def release_lease(self, profile_id: str, holder: str) -> bool:
        """Hand the lease back, but only if we still hold it.

        Guarded on the holder id so a slow teardown cannot clear the lease of
        whoever took the profile over after ours expired.
        """
        cursor = self._connection.execute(
            "UPDATE profiles SET locked_by = NULL, lock_expires = NULL "
            "WHERE id = ? AND locked_by = ?",
            (profile_id, holder),
        )
        self._connection.commit()
        return cursor.rowcount > 0

    async def force_release_lease(self, profile_id: str) -> str | None:
        """Clear a lease whoever holds it; returns the holder it was taken from.

        Read then clear, because the UPDATE cannot report the value it just
        erased. Nothing can interleave: SQLite admits one writer at a time.

        Deliberately absent from the HTTP API. A force-unlock button is the
        quickest route back to two machines on one identity, so it lives only
        behind the CLI, where reaching for it means deliberate shell access to
        the host (``camoufox-pm unlock``).
        """
        row = self._connection.execute(
            "SELECT locked_by FROM profiles WHERE id = ? AND locked_by IS NOT NULL",
            (profile_id,),
        ).fetchone()
        if row is None:
            return None
        self._connection.execute(
            "UPDATE profiles SET locked_by = NULL, lock_expires = NULL WHERE id = ?",
            (profile_id,),
        )
        self._connection.commit()
        return row["locked_by"]

    async def get_lease(self, profile_id: str) -> tuple[str | None, str | None] | None:
        """Return ``(locked_by, lock_expires)``, or ``None`` if no such profile.

        A plain read, so it reports an expired lease exactly as stored. Callers
        asking "is this held right now" pass the expiry through
        :func:`~camoufox_pm.core.leases.lease_expired`, or simply try to acquire.
        """
        cursor = self._connection.execute(
            "SELECT locked_by, lock_expires FROM profiles WHERE id = ?", (profile_id,)
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return row["locked_by"], row["lock_expires"]

    async def get_lease_holders(self) -> list[dict]:
        """Every lease in the database, expired ones included, for inspection."""
        cursor = self._connection.execute(
            """
            SELECT id, name, locked_by, lock_expires
              FROM profiles
             WHERE locked_by IS NOT NULL
             ORDER BY id
            """
        )
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "locked_by": row["locked_by"],
                "lock_expires": row["lock_expires"],
                "expired": lease_expired(row["lock_expires"]),
            }
            for row in cursor.fetchall()
        ]

    # --- Utilities ---

    def _row_to_schedule(self, row) -> Schedule:
        """Convert a database row into a Schedule object."""
        return Schedule(
            id=row["id"],
            profile_id=row["profile_id"],
            action=row["action"],
            kind=row["kind"],
            interval_minutes=row["interval_minutes"],
            at_time=row["at_time"],
            days=json.loads(row["days"]) if row["days"] else None,
            run_minutes=row["run_minutes"],
            enabled=bool(row["enabled"]),
            next_run_at=(
                datetime.fromisoformat(row["next_run_at"]) if row["next_run_at"] else None
            ),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _row_to_profile(self, row) -> Profile:
        """Convert a database row into a Profile object."""
        from .models import BrowserSettings

        # Parse browser_settings
        browser_settings_data = json.loads(row["browser_settings"])
        browser_settings = BrowserSettings(**browser_settings_data)

        # Parse proxy_config if present (password is decrypted on read).
        proxy = None
        if row["proxy_config"]:
            proxy = _deserialize_proxy(json.loads(row["proxy_config"]))

        # Parse extensions
        extensions = json.loads(row["extensions"]) if row["extensions"] else []

        # Present only once the profile has been launched, and absent entirely on
        # rows written before the column existed.
        keys = row.keys()
        fingerprint = (
            json.loads(row["fingerprint"]) if "fingerprint" in keys and row["fingerprint"] else None
        )
        stored_check = None
        if "proxy_check" in keys and row["proxy_check"]:
            try:
                stored_check = ProxyCheckRecord.model_validate_json(row["proxy_check"])
            except ValidationError as error:
                # A cosmetic column must not be able to take down the list: this
                # runs for every row, so raising here 500s the whole screen over
                # one unreadable value. Losing the dot is the right cost.
                logger.warning(f"Profile {row['id']}: unreadable proxy check, ignoring ({error})")

        return Profile(
            id=row["id"],
            name=row["name"],
            group=row["group_id"],
            status=ProfileStatus(row["status"]),
            browser_settings=browser_settings,
            proxy=proxy,
            extensions=extensions,
            storage_path=row["storage_path"],
            notes=row["notes"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            last_used=datetime.fromisoformat(row["last_used"]) if row["last_used"] else None,
            fingerprint=fingerprint,
            proxy_check=stored_check,
            row_version=row["row_version"],
        )

    async def close(self):
        """Close the database connection."""
        if self._connection:
            self._connection.close()
            self._connection: sqlite3.Connection = None  # type: ignore[assignment]
        logger.info("Database connection closed")


class StorageManager:
    """StorageManager backed by the SQLite database."""

    def __init__(self, db_path: str = "data/profiles.db"):
        self.db = DatabaseManager(db_path)
        logger.info(f"StorageManager initialized with database: {db_path}")

    async def initialize(self):
        """Initialize the database."""
        await self.db.initialize()

    # Profile methods
    async def save_profile(self, profile: Profile, expected_row_version: int | None = None):
        await self.db.save_profile(profile, expected_row_version)

    async def get_profile(self, profile_id: str) -> Profile | None:
        return await self.db.get_profile(profile_id)

    async def update_profile(self, profile: Profile, expected_row_version: int | None = None):
        await self.db.update_profile(profile, expected_row_version)

    async def set_proxy_check(self, profile_id: str, record: ProxyCheckRecord | None) -> None:
        await self.db.set_proxy_check(profile_id, record)

    async def set_launch_pin(
        self, profile_id: str, fingerprint: dict | None, last_used: datetime
    ) -> None:
        await self.db.set_launch_pin(profile_id, fingerprint, last_used)

    async def delete_profile(self, profile_id: str) -> bool:
        return await self.db.delete_profile(profile_id)

    async def list_profiles(
        self, filters: dict | None = None, limit: int | None = None, offset: int = 0
    ) -> list[Profile]:
        return await self.db.list_profiles(filters, limit, offset)

    async def count_profiles(self, filters: dict | None = None) -> int:
        return await self.db.count_profiles(filters)

    # Group methods
    async def save_profile_group(self, group: ProfileGroup):
        await self.db.save_profile_group(group)

    async def list_profile_groups(self) -> list[ProfileGroup]:
        return await self.db.list_profile_groups()

    # Statistics methods
    async def log_usage(self, usage_stats: UsageStats):
        await self.db.log_usage(usage_stats)

    async def get_profile_usage_stats(self, profile_id: str) -> list[UsageStats]:
        return await self.db.get_profile_usage_stats(profile_id)

    async def delete_profile_group(self, group_id: str) -> bool:
        return await self.db.delete_profile_group(group_id)

    # User and session methods
    async def create_user(self, user_id: str, username: str, password_hash: str) -> None:
        await self.db.create_user(user_id, username, password_hash)

    async def get_user_by_username(self, username: str) -> dict | None:
        return await self.db.get_user_by_username(username)

    async def update_user_password(self, username: str, password_hash: str) -> bool:
        return await self.db.update_user_password(username, password_hash)

    async def delete_user(self, username: str) -> bool:
        return await self.db.delete_user(username)

    async def count_users(self) -> int:
        return await self.db.count_users()

    async def list_users(self) -> list[dict]:
        return await self.db.list_users()

    async def create_session(self, token_hash: str, user_id: str, expires_at: datetime) -> None:
        await self.db.create_session(token_hash, user_id, expires_at)

    async def get_session(self, token_hash: str) -> dict | None:
        return await self.db.get_session(token_hash)

    async def delete_session(self, token_hash: str) -> bool:
        return await self.db.delete_session(token_hash)

    async def delete_expired_sessions(self) -> int:
        return await self.db.delete_expired_sessions()

    # Schedule methods
    async def save_schedule(self, schedule: Schedule):
        await self.db.save_schedule(schedule)

    async def get_schedule(self, schedule_id: str) -> Schedule | None:
        return await self.db.get_schedule(schedule_id)

    async def list_schedules(self, profile_id: str | None = None) -> list[Schedule]:
        return await self.db.list_schedules(profile_id)

    async def delete_schedule(self, schedule_id: str) -> bool:
        return await self.db.delete_schedule(schedule_id)

    async def log_schedule_run(self, run: ScheduleRun) -> ScheduleRun:
        return await self.db.log_schedule_run(run)

    async def list_schedule_runs(self, schedule_id: str, limit: int = 20) -> list[ScheduleRun]:
        return await self.db.list_schedule_runs(schedule_id, limit)

    # Lease methods
    async def acquire_lease(self, profile_id: str, holder: str, ttl_seconds: int) -> bool:
        return await self.db.acquire_lease(profile_id, holder, ttl_seconds)

    async def renew_lease(self, profile_ids: list[str], holder: str, ttl_seconds: int) -> int:
        return await self.db.renew_lease(profile_ids, holder, ttl_seconds)

    async def release_lease(self, profile_id: str, holder: str) -> bool:
        return await self.db.release_lease(profile_id, holder)

    async def force_release_lease(self, profile_id: str) -> str | None:
        return await self.db.force_release_lease(profile_id)

    async def get_lease(self, profile_id: str) -> tuple[str | None, str | None] | None:
        return await self.db.get_lease(profile_id)

    async def get_lease_holders(self) -> list[dict]:
        return await self.db.get_lease_holders()

    async def close(self):
        """Close the database."""
        await self.db.close()
