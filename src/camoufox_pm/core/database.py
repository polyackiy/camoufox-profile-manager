"""
SQLite storage layer for the Camoufox profile management system.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from loguru import logger

from .crypto import decrypt, encrypt
from .models import (
    Profile,
    ProfileGroup,
    ProfileStatus,
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
            "profiles": [("fingerprint", "TEXT")],
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
                fingerprint TEXT
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

    async def save_profile(self, profile: Profile):
        """Save a profile to the database."""
        self._connection.execute(
            """
            INSERT OR REPLACE INTO profiles (
                id, name, group_id, status, browser_settings, proxy_config,
                extensions, storage_path, notes, created_at, updated_at, last_used,
                fingerprint
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                profile.created_at.isoformat(),
                profile.updated_at.isoformat(),
                profile.last_used.isoformat() if profile.last_used else None,
                json.dumps(profile.fingerprint) if profile.fingerprint else None,
            ),
        )
        self._connection.commit()
        logger.debug(f"Profile {profile.name} saved")

    async def get_profile(self, profile_id: str) -> Profile | None:
        """Get a profile by ID."""
        cursor = self._connection.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,))
        row = cursor.fetchone()

        if row:
            return self._row_to_profile(row)
        return None

    async def update_profile(self, profile: Profile):
        """Update a profile."""
        profile.updated_at = datetime.now()
        await self.save_profile(profile)
        logger.debug(f"Profile {profile.name} updated")

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
    async def save_profile(self, profile: Profile):
        await self.db.save_profile(profile)

    async def get_profile(self, profile_id: str) -> Profile | None:
        return await self.db.get_profile(profile_id)

    async def update_profile(self, profile: Profile):
        await self.db.update_profile(profile)

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

    async def close(self):
        """Close the database."""
        await self.db.close()
