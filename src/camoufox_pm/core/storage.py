"""Storage backend selection, lease errors, and the PostgreSQL backend.

Two backends live behind one ``StorageManager`` façade:

- ``CPM_DB_URL`` unset — SQLite, exactly as before, in the file named by
  ``CPM_DB_PATH``. This is the default and keeps single-machine installs and
  the test suite unchanged.
- ``CPM_DB_URL`` set — PostgreSQL (psycopg3), so several CFPM instances on
  different machines can share one database.

The database is the only state two machines sharing profiles must agree on.
The SQLite backend has no answer to "is this profile running?" beyond the
current process's memory, so the Postgres backend adds row-level leases:

- ``locked_by`` — holder id, ``"<hostname>:<pid>:<uuid4>"``; NULL means free.
- ``lock_expires`` — absolute expiry; an expired lease is as good as no lease,
  which is how a crashed machine releases its profiles without operator action.
- ``row_version`` — bumped on every save; a save that expected an older
  version is refused instead of silently clobbering a concurrent edit.

Only ``DatabaseManager`` is backend-coupled; everything else talks to the
``StorageManager`` façade.
"""

import asyncio
import json
from datetime import datetime
from typing import Any

from loguru import logger
from pydantic import ValidationError

from .crypto import decrypt, encrypt
from .errors import StaleWriteError
from .models import (
    BrowserSettings,
    Profile,
    ProfileGroup,
    ProfileStatus,
    ProxyCheckRecord,
    ProxyConfig,
    Schedule,
    ScheduleRun,
    UsageStats,
)

try:
    import psycopg
    from psycopg import Error as PsycopgError
    from psycopg.rows import dict_row

    PSYCOPG_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without psycopg installed
    psycopg = None  # type: ignore[assignment]
    PsycopgError = None  # type: ignore[assignment,misc]
    dict_row = None  # type: ignore[assignment,misc]
    PSYCOPG_AVAILABLE = False


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


class PostgresDatabaseManager:
    """PostgreSQL storage backend, shaped like the SQLite ``DatabaseManager``.

    psycopg matches that manager's shape closely (``connect``, ``row_factory``,
    ``cursor.execute``), so this mirrors its method set and semantics without
    an ORM.
    """

    def __init__(self, db_url: str):
        if not PSYCOPG_AVAILABLE:
            raise RuntimeError(
                "PostgreSQL support requires psycopg; "
                "install camoufox-pm[postgres] or set CPM_DB_URL back to SQLite"
            )
        self.db_url = db_url
        self._connection: Any = None
        logger.info("DatabaseManager initialized with PostgreSQL backend")

    async def initialize(self):
        """Connect and create tables; existing tables are left as they are.

        One connection, like the SQLite backend: all calls run through one
        event loop, and psycopg serialises concurrent statements on a single
        connection, which one CFPM instance per machine never comes near
        saturating.

        ``client_encoding=UTF8`` is forced because a cluster initialised with
        ``SQL_ASCII`` (a common default for throwaway containers) otherwise
        hands every text column back as ``bytes``.
        """
        self._connection = psycopg.connect(
            self.db_url, row_factory=dict_row, client_encoding="UTF8"
        )
        self._connection.autocommit = True

        await self._create_tables()
        await self._migrate()
        await self._create_indexes()
        logger.info("Database initialized")

    async def _migrate(self):
        """Bring an existing database up to the current schema.

        The SQLite ``_migrate`` scans ``PRAGMA table_info`` because SQLite
        cannot express "add this column if missing". PostgreSQL can:
        ``ADD COLUMN IF NOT EXISTS`` is itself re-runnable, so each entry here
        is one statement and never touches existing rows.
        """
        added_columns = {
            "profiles": [("fingerprint", "JSONB"), ("proxy_check", "TEXT")],
        }
        for table, columns in added_columns.items():
            for name, column_type in columns:
                self._connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {column_type}"
                )
                logger.info(f"Migrated {table}: ensured column {name}")

    async def _create_tables(self):
        """Create the database tables."""
        # Profiles table. JSONB for fingerprint and browser_settings — the two
        # columns with plausible structured queries (e.g.
        # fingerprint->>'navigator.platform'). proxy_config stays TEXT: it holds
        # Fernet ciphertext whenever CPM_SECRET_KEY is set, not JSON, so a
        # JSONB column there would corrupt the secrets.
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS profiles (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                group_id TEXT,
                status TEXT DEFAULT 'active',
                browser_settings JSONB NOT NULL,
                proxy_config TEXT,
                extensions TEXT,
                storage_path TEXT,
                notes TEXT,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                last_used TIMESTAMP,
                fingerprint JSONB,
                proxy_check TEXT,
                locked_by TEXT,
                lock_expires TIMESTAMPTZ,
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
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                profile_id TEXT NOT NULL,
                action TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT now(),
                duration INTEGER,
                success BOOLEAN DEFAULT TRUE,
                details TEXT
            )
        """)

        # User accounts for the web UI. Their mere existence turns login on, so a
        # database without rows here behaves exactly as before the table existed.
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT now()
            )
        """)
        # SQLite kept usernames unique case-insensitively via COLLATE NOCASE;
        # the equivalent Postgres shape is a unique index on a lowercased
        # expression, which also backs the lower(username) lookups below.
        self._connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_lower ON users (lower(username))"
        )

        # Login sessions, keyed by the SHA-256 of the cookie token — the token
        # itself is never stored, so this table cannot be replayed if leaked.
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TIMESTAMP DEFAULT now(),
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
                enabled BOOLEAN DEFAULT TRUE,
                next_run_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT now(),
                updated_at TIMESTAMP DEFAULT now()
            )
        """)

        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS schedule_runs (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                schedule_id TEXT NOT NULL,
                started_at TIMESTAMP NOT NULL,
                finished_at TIMESTAMP,
                outcome TEXT NOT NULL,
                message TEXT
            )
        """)

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

    # --- Profiles ---

    async def save_profile(self, profile: Profile, expected_row_version: int | None = None):
        """Insert or update a profile.

        The SQLite backend replaces the whole row (INSERT OR REPLACE), which
        two machines sharing this database would use to clobber each other's
        edits — and its lease columns. Here a creation is a plain insert, and
        replacing an existing row preserves the lease columns.

        With ``expected_row_version`` the update carries
        ``AND row_version = <expected>`` and bumps the counter; zero rows
        updated raises ``StaleWriteError`` rather than clobbering the winner.
        That is what makes editing one profile from two instances safe,
        independently of the lease.
        """
        if expected_row_version is None:
            await self._overwrite_profile(profile)
        else:
            cursor = self._connection.execute(
                """
                UPDATE profiles SET
                    name = %s, group_id = %s, status = %s, browser_settings = %s,
                    proxy_config = %s, extensions = %s, storage_path = %s, notes = %s,
                    created_at = %s, updated_at = %s, last_used = %s, fingerprint = %s,
                    proxy_check = %s, row_version = row_version + 1
                WHERE id = %s AND row_version = %s
                """,
                (
                    profile.name,
                    profile.group,
                    profile.status.value if hasattr(profile.status, "value") else profile.status,
                    json.dumps(profile.browser_settings.model_dump()),
                    json.dumps(_serialize_proxy(profile.proxy)) if profile.proxy else None,
                    json.dumps(profile.extensions),
                    profile.storage_path,
                    profile.notes,
                    profile.created_at,
                    profile.updated_at,
                    profile.last_used,
                    json.dumps(profile.fingerprint) if profile.fingerprint else None,
                    profile.proxy_check.model_dump_json() if profile.proxy_check else None,
                    profile.id,
                    expected_row_version,
                ),
            )
            if cursor.rowcount == 0:
                raise StaleWriteError(profile.id, expected_row_version)
            profile.row_version = expected_row_version + 1
        logger.debug(f"Profile {profile.name} saved")

    async def update_profile(self, profile: Profile, expected_row_version: int | None = None):
        """Update a profile.

        With ``expected_row_version`` this is the optimistic-concurrency write:
        it lands only when the stored version still matches what the caller
        read, and bumps the counter. Without one the row is overwritten as the
        SQLite backend did, for callers that re-read immediately before
        writing. See ``save_profile`` for the details.
        """
        await self.save_profile(profile, expected_row_version)

    async def _overwrite_profile(self, profile: Profile) -> None:
        """Insert or replace the whole row, preserving the lease columns.

        The Postgres translation of SQLite's INSERT OR REPLACE. The lease and
        row-version columns survive via a re-read of the existing row: a save
        must never break or take another holder's lease, and must not reset a
        row version a concurrent editor is relying on. Creation inserts read
        those subselects as NULL — the defaults — because the row does not
        exist yet.
        """
        self._connection.execute(
            """
            INSERT INTO profiles (
                id, name, group_id, status, browser_settings, proxy_config,
                extensions, storage_path, notes, created_at, updated_at, last_used,
                fingerprint, proxy_check, locked_by, lock_expires, row_version
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                      (SELECT locked_by FROM profiles WHERE id = %s),
                      (SELECT lock_expires FROM profiles WHERE id = %s),
                      COALESCE((SELECT row_version FROM profiles WHERE id = %s), 0))
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                group_id = EXCLUDED.group_id,
                status = EXCLUDED.status,
                browser_settings = EXCLUDED.browser_settings,
                proxy_config = EXCLUDED.proxy_config,
                extensions = EXCLUDED.extensions,
                storage_path = EXCLUDED.storage_path,
                notes = EXCLUDED.notes,
                created_at = EXCLUDED.created_at,
                updated_at = EXCLUDED.updated_at,
                last_used = EXCLUDED.last_used,
                fingerprint = EXCLUDED.fingerprint,
                proxy_check = EXCLUDED.proxy_check
            """,
            (
                profile.id,
                profile.name,
                profile.group,
                profile.status.value if hasattr(profile.status, "value") else profile.status,
                json.dumps(profile.browser_settings.model_dump()),
                json.dumps(_serialize_proxy(profile.proxy)) if profile.proxy else None,
                json.dumps(profile.extensions),
                profile.storage_path,
                profile.notes,
                profile.created_at,
                profile.updated_at,
                profile.last_used,
                json.dumps(profile.fingerprint) if profile.fingerprint else None,
                profile.proxy_check.model_dump_json() if profile.proxy_check else None,
                profile.id,
                profile.id,
                profile.id,
            ),
        )

    async def get_profile(self, profile_id: str) -> Profile | None:
        """Get a profile by ID."""
        cursor = self._connection.execute("SELECT * FROM profiles WHERE id = %s", (profile_id,))
        row = cursor.fetchone()

        if row:
            return self._row_to_profile(row)
        return None

    async def set_proxy_check(self, profile_id: str, record: ProxyCheckRecord | None) -> None:
        """Write only the proxy check, leaving every other column alone.

        Mirrors the SQLite method: a check takes seconds — up to thirty against
        a proxy that never answers — and writing back a Profile read before
        that wait would revert anything edited during it. It also keeps a check
        from touching ``updated_at``: asking a proxy a question is not an edit,
        and a bulk check should not make a selection look modified.
        """
        self._connection.execute(
            "UPDATE profiles SET proxy_check = %s WHERE id = %s",
            (record.model_dump_json() if record else None, profile_id),
        )

    async def delete_profile(self, profile_id: str) -> bool:
        """Delete a profile, and the schedules that would otherwise fire against it."""
        self._connection.execute(
            "DELETE FROM schedule_runs WHERE schedule_id IN "
            "(SELECT id FROM schedules WHERE profile_id = %s)",
            (profile_id,),
        )
        self._connection.execute("DELETE FROM schedules WHERE profile_id = %s", (profile_id,))
        cursor = self._connection.execute("DELETE FROM profiles WHERE id = %s", (profile_id,))
        deleted = cursor.rowcount > 0

        if deleted:
            logger.debug(f"Profile {profile_id} deleted")

        return deleted

    async def list_profiles(
        self, filters: dict | None = None, limit: int | None = None, offset: int = 0
    ) -> list[Profile]:
        """List profiles with optional filtering."""
        query = "SELECT * FROM profiles WHERE 1=1"
        params: list[Any] = []

        if filters:
            if "group" in filters:
                query += " AND group_id = %s"
                params.append(filters["group"])
            if "status" in filters:
                query += " AND status = %s"
                params.append(filters["status"])
            if "name_like" in filters:
                # ILIKE rather than LIKE: Postgres is case-sensitive here, and a
                # search box that finds "Facebook" only when typed exactly would
                # be a regression against SQLite's case-insensitive LIKE.
                query += " AND name ILIKE %s"
                params.append(f"%{filters['name_like']}%")

        query += " ORDER BY created_at DESC"

        if limit or offset:
            query += " LIMIT %s"
            params.append(limit if limit else -1)
        if offset:
            query += " OFFSET %s"
            params.append(offset)

        cursor = self._connection.execute(query, params)
        rows = cursor.fetchall()

        return [self._row_to_profile(row) for row in rows]

    async def count_profiles(self, filters: dict | None = None) -> int:
        """Count profiles."""
        query = "SELECT COUNT(*) AS n FROM profiles WHERE 1=1"
        params: list[Any] = []

        if filters:
            if "group" in filters:
                query += " AND group_id = %s"
                params.append(filters["group"])
            if "status" in filters:
                query += " AND status = %s"
                params.append(filters["status"])

        cursor = self._connection.execute(query, params)
        return cursor.fetchone()["n"]

    # --- Profile groups ---

    async def save_profile_group(self, group: ProfileGroup):
        """Save a profile group."""
        self._connection.execute(
            """
            INSERT INTO profile_groups (
                id, name, description, profile_count, created_at
            ) VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                description = EXCLUDED.description,
                profile_count = EXCLUDED.profile_count,
                created_at = EXCLUDED.created_at
            """,
            (
                group.id,
                group.name,
                group.description,
                group.profile_count,
                group.created_at,
            ),
        )
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
                created_at=row["created_at"],
            )
            groups.append(group)

        return groups

    async def delete_profile_group(self, group_id: str) -> bool:
        """Delete a profile group."""
        # Ungroup the profiles that belonged to this group
        self._connection.execute(
            "UPDATE profiles SET group_id = NULL WHERE group_id = %s", (group_id,)
        )

        # Delete the group
        cursor = self._connection.execute("DELETE FROM profile_groups WHERE id = %s", (group_id,))

        return cursor.rowcount > 0

    # --- Statistics ---

    async def log_usage(self, usage_stats: UsageStats):
        """Record a usage statistic."""
        row = self._connection.execute(
            """
            INSERT INTO usage_stats (
                profile_id, action, timestamp, duration, success, details
            ) VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                usage_stats.profile_id,
                usage_stats.action,
                usage_stats.timestamp,
                usage_stats.duration,
                usage_stats.success,
                json.dumps(usage_stats.details) if usage_stats.details else None,
            ),
        ).fetchone()
        usage_stats.id = row["id"]

    async def get_profile_usage_stats(self, profile_id: str, limit: int = 100) -> list[UsageStats]:
        """Get usage statistics for a profile."""
        cursor = self._connection.execute(
            """
            SELECT * FROM usage_stats
            WHERE profile_id = %s
            ORDER BY timestamp DESC
            LIMIT %s
        """,
            (profile_id, limit),
        )

        return [
            UsageStats(
                id=row["id"],
                profile_id=row["profile_id"],
                action=row["action"],
                timestamp=row["timestamp"],
                duration=row["duration"],
                success=bool(row["success"]),
                details=row["details"] if row["details"] else None,
            )
            for row in cursor.fetchall()
        ]

    # --- Users and sessions ---

    async def create_user(self, user_id: str, username: str, password_hash: str) -> None:
        """Create a user; raises ``ValueError`` when the username is taken."""
        try:
            self._connection.execute(
                "INSERT INTO users (id, username, password_hash, created_at) "
                "VALUES (%s, %s, %s, %s)",
                (user_id, username, password_hash, datetime.now()),
            )
        except PsycopgError as exc:
            if not self._is_unique_violation(exc):
                raise
            raise ValueError(f"User {username!r} already exists") from exc
        # Deliberately logs the name only, never the hash.
        logger.info(f"User {username} created")

    @staticmethod
    def _is_unique_violation(exc: Exception) -> bool:
        """Whether this database error is a unique-constraint violation.

        Matched by SQLSTATE rather than exception type: unlike SQLite, psycopg
        raises one error class for every server-side failure.
        """
        return getattr(exc, "sqlstate", None) == "23505"

    async def get_user_by_username(self, username: str) -> dict | None:
        cursor = self._connection.execute(
            "SELECT id, username, password_hash FROM users WHERE lower(username) = lower(%s)",
            (username,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    async def update_user_password(self, username: str, password_hash: str) -> bool:
        cursor = self._connection.execute(
            "UPDATE users SET password_hash = %s WHERE lower(username) = lower(%s)",
            (password_hash, username),
        )
        return cursor.rowcount > 0

    async def delete_user(self, username: str) -> bool:
        """Delete a user; the foreign key cascades their open sessions away."""
        cursor = self._connection.execute(
            "DELETE FROM users WHERE lower(username) = lower(%s)", (username,)
        )
        return cursor.rowcount > 0

    async def count_users(self) -> int:
        cursor = self._connection.execute("SELECT COUNT(*) AS n FROM users")
        return cursor.fetchone()["n"]

    async def list_users(self) -> list[dict]:
        """Usernames and creation times only — hashes stay in the table."""
        cursor = self._connection.execute(
            "SELECT username, created_at FROM users ORDER BY created_at"
        )
        return [dict(row) for row in cursor.fetchall()]

    async def create_session(self, token_hash: str, user_id: str, expires_at: datetime) -> None:
        self._connection.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) "
            "VALUES (%s, %s, %s, %s)",
            (token_hash, user_id, datetime.now(), expires_at),
        )

    async def get_session(self, token_hash: str) -> dict | None:
        cursor = self._connection.execute(
            """
            SELECT s.token_hash, s.user_id, s.expires_at, u.username
            FROM sessions s JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = %s
            """,
            (token_hash,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    async def delete_session(self, token_hash: str) -> bool:
        cursor = self._connection.execute(
            "DELETE FROM sessions WHERE token_hash = %s", (token_hash,)
        )
        return cursor.rowcount > 0

    async def delete_expired_sessions(self) -> int:
        cursor = self._connection.execute(
            "DELETE FROM sessions WHERE expires_at <= %s", (datetime.now(),)
        )
        return cursor.rowcount

    # --- Schedules ---

    async def save_schedule(self, schedule: Schedule):
        """Insert or replace a schedule."""
        self._connection.execute(
            """
            INSERT INTO schedules (
                id, profile_id, action, kind, interval_minutes, at_time, days,
                run_minutes, enabled, next_run_at, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                profile_id = EXCLUDED.profile_id,
                action = EXCLUDED.action,
                kind = EXCLUDED.kind,
                interval_minutes = EXCLUDED.interval_minutes,
                at_time = EXCLUDED.at_time,
                days = EXCLUDED.days,
                run_minutes = EXCLUDED.run_minutes,
                enabled = EXCLUDED.enabled,
                next_run_at = EXCLUDED.next_run_at,
                updated_at = EXCLUDED.updated_at
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
                schedule.next_run_at,
                schedule.created_at,
                schedule.updated_at,
            ),
        )

    async def get_schedule(self, schedule_id: str) -> Schedule | None:
        """Get a schedule by ID."""
        cursor = self._connection.execute("SELECT * FROM schedules WHERE id = %s", (schedule_id,))
        row = cursor.fetchone()
        return self._row_to_schedule(row) if row else None

    async def list_schedules(self, profile_id: str | None = None) -> list[Schedule]:
        """List schedules, oldest first so the UI order is stable."""
        if profile_id:
            cursor = self._connection.execute(
                "SELECT * FROM schedules WHERE profile_id = %s ORDER BY created_at",
                (profile_id,),
            )
        else:
            cursor = self._connection.execute("SELECT * FROM schedules ORDER BY created_at")
        return [self._row_to_schedule(row) for row in cursor.fetchall()]

    async def delete_schedule(self, schedule_id: str) -> bool:
        """Delete a schedule and its run history."""
        self._connection.execute("DELETE FROM schedule_runs WHERE schedule_id = %s", (schedule_id,))
        cursor = self._connection.execute("DELETE FROM schedules WHERE id = %s", (schedule_id,))
        return cursor.rowcount > 0

    async def log_schedule_run(self, run: ScheduleRun, keep: int = 20) -> ScheduleRun:
        """Record one firing and prune the history to the newest ``keep`` rows.

        Bounded per schedule rather than by age: an every-five-minutes schedule
        would otherwise write hundreds of rows a day into a database that also
        holds the profiles.
        """
        row = self._connection.execute(
            """
            INSERT INTO schedule_runs (schedule_id, started_at, finished_at, outcome, message)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                run.schedule_id,
                run.started_at,
                run.finished_at,
                run.outcome,
                run.message,
            ),
        ).fetchone()
        run.id = row["id"]
        self._connection.execute(
            """
            DELETE FROM schedule_runs WHERE schedule_id = %s AND id NOT IN (
                SELECT id FROM schedule_runs WHERE schedule_id = %s ORDER BY id DESC LIMIT %s
            )
            """,
            (run.schedule_id, run.schedule_id, keep),
        )
        return run

    async def list_schedule_runs(self, schedule_id: str, limit: int = 20) -> list[ScheduleRun]:
        """Get the newest runs of a schedule, newest first."""
        cursor = self._connection.execute(
            "SELECT * FROM schedule_runs WHERE schedule_id = %s ORDER BY id DESC LIMIT %s",
            (schedule_id, limit),
        )
        return [
            ScheduleRun(
                id=row["id"],
                schedule_id=row["schedule_id"],
                started_at=row["started_at"],
                finished_at=row["finished_at"],
                outcome=row["outcome"],
                message=row["message"],
            )
            for row in cursor.fetchall()
        ]

    # --- Leases -------------------------------------------------------------

    async def acquire_lease(self, profile_id: str, holder: str, ttl_seconds: int) -> bool:
        """Try to lease a profile in one atomic statement.

        The conditional UPDATE takes the row lock and re-evaluates the guard
        under it, so two machines racing on a free profile produce exactly one
        winner even under READ COMMITTED. Re-acquiring with the same holder id
        succeeds: a restart on the same host must be able to pick its lease up
        again rather than wait out its own TTL.

        Returns ``False`` — without touching the row — when it is held
        elsewhere and not expired; callers turn that into ``ProfileLocked``.
        """
        cursor = self._connection.execute(
            """
            UPDATE profiles
               SET locked_by = %s,
                   lock_expires = now() + %s * interval '1 second'
             WHERE id = %s
               AND (locked_by IS NULL OR lock_expires < now() OR locked_by = %s)
            RETURNING id
            """,
            (holder, ttl_seconds, profile_id, holder),
        )
        return cursor.fetchone() is not None

    async def renew_lease(self, profile_ids: list[str], holder: str, ttl_seconds: int) -> int:
        """Push the expiry of this holder's leases out; returns how many held.

        One heartbeat statement for all of the instance's active sessions. A
        profile missing from the result has been taken over (or expired) — the
        caller treats the lease as lost rather than trying to win it back.
        """
        cursor = self._connection.execute(
            """
            UPDATE profiles
               SET lock_expires = now() + %s * interval '1 second'
             WHERE id = ANY(%s) AND locked_by = %s
            """,
            (ttl_seconds, profile_ids, holder),
        )
        return cursor.rowcount

    async def acquire_concurrently(
        self, profile_id: str, holders: list[str], ttl_seconds: int
    ) -> list[str]:
        """Race ``acquire_lease`` from one real connection per holder.

        Exists for the concurrency test: the whole point of the conditional
        UPDATE is that two machines racing on one row produce one winner, and
        that claim is only worth its test output if it is executed with
        genuine contention. Returns the holder ids that won.
        """

        async def one(holder: str) -> str | None:
            conn = psycopg.connect(self.db_url, row_factory=dict_row)
            conn.autocommit = True
            try:
                cursor = conn.execute(
                    """
                    UPDATE profiles
                       SET locked_by = %s,
                           lock_expires = now() + %s * interval '1 second'
                     WHERE id = %s
                       AND (locked_by IS NULL OR lock_expires < now() OR locked_by = %s)
                    RETURNING id
                    """,
                    (holder, ttl_seconds, profile_id, holder),
                )
                return holder if cursor.fetchone() is not None else None
            finally:
                conn.close()

        results = await asyncio.gather(*(one(holder) for holder in holders))
        return [holder for holder in results if holder is not None]

    async def release_lease(self, profile_id: str, holder: str) -> bool:
        """Clear the lease, but only if we still hold it.

        Guarded by the holder id: after a stolen lease the new holder's lease
        must not be broken by this holder's slow teardown.
        """
        cursor = self._connection.execute(
            """
            UPDATE profiles
               SET locked_by = NULL, lock_expires = NULL
             WHERE id = %s AND locked_by = %s
            """,
            (profile_id, holder),
        )
        return cursor.rowcount > 0

    async def force_release_lease(self, profile_id: str) -> str | None:
        """Clear the lease whatever it says; returns the holder it was taken from.

        Deliberately absent from the HTTP API: a "force unlock" button is the
        fastest route to the two-machines-one-identity corruption the lease
        exists to prevent. It lives behind the CLI, where using it is a
        deliberate act on the host itself.

        Read then clear, like the SQLite sibling: Postgres's RETURNING also
        sees the new row, so the cleared column would come back NULL. The
        statements share one connection, so nothing can interleave.
        """
        row = self._connection.execute(
            "SELECT locked_by FROM profiles WHERE id = %s AND locked_by IS NOT NULL",
            (profile_id,),
        ).fetchone()
        if row is None:
            return None
        self._connection.execute(
            "UPDATE profiles SET locked_by = NULL, lock_expires = NULL WHERE id = %s",
            (profile_id,),
        )
        return row["locked_by"]

    async def get_lease(self, profile_id: str) -> tuple[str | None, datetime | None] | None:
        """Return ``(locked_by, lock_expires)`` for a profile, or ``None``."""
        cursor = self._connection.execute(
            "SELECT locked_by, lock_expires FROM profiles WHERE id = %s", (profile_id,)
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return row["locked_by"], row["lock_expires"]

    async def get_lease_holders(self) -> list[dict[str, Any]]:
        """Every lease in the store, expired ones included, for inspection tools."""
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
                "expired": row["lock_expires"] is not None
                and row["lock_expires"] < datetime.now(row["lock_expires"].tzinfo),
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
            next_run_at=row["next_run_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_profile(self, row) -> Profile:
        """Convert a database row into a Profile object."""
        # JSONB columns already arrive as Python objects; json.loads would be a
        # str error. Other columns stay TEXT and parse as before.
        browser_settings = BrowserSettings(**row["browser_settings"])

        # Parse proxy_config if present (password is decrypted on read).
        proxy = None
        if row["proxy_config"]:
            proxy = _deserialize_proxy(json.loads(row["proxy_config"]))

        # Parse extensions
        extensions = json.loads(row["extensions"]) if row["extensions"] else []

        stored_check = None
        if row["proxy_check"]:
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
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_used=row["last_used"],
            fingerprint=row["fingerprint"],
            proxy_check=stored_check,
            row_version=row["row_version"],
        )

    async def close(self):
        """Close the database connection."""
        if self._connection:
            self._connection.close()
            self._connection = None
        logger.info("Database connection closed")
