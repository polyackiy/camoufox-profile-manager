"""
Полноценная реализация базы данных SQLite для системы управления профилями Camoufox
"""

import sqlite3
import json
import asyncio
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any, Union
from contextlib import asynccontextmanager
from loguru import logger

from .models import (
    Profile, ProfileGroup, UsageStats, ProxyConfig, 
    ProfileStatus, ProxyTestResult, SystemStatus
)


class DatabaseManager:
    """Менеджер базы данных SQLite с async поддержкой"""
    
    def __init__(self, db_path: str = "data/profiles.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = None
        logger.info(f"Инициализирован DatabaseManager с БД: {self.db_path}")
    
    async def initialize(self):
        """Инициализация базы данных и создание таблиц"""
        self._connection = sqlite3.connect(str(self.db_path), timeout=30.0)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        
        await self._create_tables()
        await self._create_indexes()
        logger.info("База данных инициализирована")
    
    async def _create_tables(self):
        """Создание таблиц базы данных"""
        # Таблица профилей
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
                last_used TIMESTAMP
            )
        """)
        
        # Таблица групп профилей  
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS profile_groups (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                profile_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Таблица статистики использования
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
        
        self._connection.commit()
    
    async def _create_indexes(self):
        """Создание индексов для оптимизации запросов"""
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_profiles_group ON profiles(group_id)",
            "CREATE INDEX IF NOT EXISTS idx_profiles_status ON profiles(status)",
            "CREATE INDEX IF NOT EXISTS idx_profiles_created ON profiles(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_usage_stats_profile ON usage_stats(profile_id)",
            "CREATE INDEX IF NOT EXISTS idx_usage_stats_timestamp ON usage_stats(timestamp)",
        ]
        
        for index_sql in indexes:
            self._connection.execute(index_sql)
        
        self._connection.commit()
    
    # === ПРОФИЛИ ===
    
    async def save_profile(self, profile: Profile):
        """Сохранение профиля в базе данных"""
        self._connection.execute("""
            INSERT OR REPLACE INTO profiles (
                id, name, group_id, status, browser_settings, proxy_config,
                extensions, storage_path, notes, created_at, updated_at, last_used
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            profile.id, profile.name, profile.group, 
            profile.status.value if hasattr(profile.status, 'value') else profile.status,
            json.dumps(profile.browser_settings.dict()),
            json.dumps(profile.proxy.dict()) if profile.proxy else None,
            json.dumps(profile.extensions),
            profile.storage_path, profile.notes,
            profile.created_at.isoformat(), profile.updated_at.isoformat(),
            profile.last_used.isoformat() if profile.last_used else None
        ))
        self._connection.commit()
        logger.debug(f"Профиль {profile.name} сохранен в БД")
    
    async def get_profile(self, profile_id: str) -> Optional[Profile]:
        """Получение профиля по ID"""
        cursor = self._connection.execute(
            "SELECT * FROM profiles WHERE id = ?", (profile_id,)
        )
        row = cursor.fetchone()
        
        if row:
            return self._row_to_profile(row)
        return None
    
    async def update_profile(self, profile: Profile):
        """Обновление профиля"""
        profile.updated_at = datetime.now()
        await self.save_profile(profile)
        logger.debug(f"Профиль {profile.name} обновлен в БД")
    
    async def delete_profile(self, profile_id: str) -> bool:
        """Удаление профиля"""
        cursor = self._connection.execute(
            "DELETE FROM profiles WHERE id = ?", (profile_id,)
        )
        self._connection.commit()
        deleted = cursor.rowcount > 0
        
        if deleted:
            logger.debug(f"Профиль {profile_id} удален из БД")
        
        return deleted
    
    async def list_profiles(self, 
                          filters: Optional[Dict] = None,
                          limit: Optional[int] = None,
                          offset: int = 0) -> List[Profile]:
        """Получение списка профилей с фильтрацией"""
        query = "SELECT * FROM profiles WHERE 1=1"
        params = []
        
        if filters:
            if 'group' in filters:
                query += " AND group_id = ?"
                params.append(filters['group'])
            if 'status' in filters:
                query += " AND status = ?"
                params.append(filters['status'])
            if 'name_like' in filters:
                query += " AND name LIKE ?"
                params.append(f"%{filters['name_like']}%")
        
        query += " ORDER BY created_at DESC"
        
        if limit:
            query += " LIMIT ?"
            params.append(limit)
            if offset:
                query += " OFFSET ?"
                params.append(offset)
        
        cursor = self._connection.execute(query, params)
        rows = cursor.fetchall()
        
        return [self._row_to_profile(row) for row in rows]
    
    async def count_profiles(self, filters: Optional[Dict] = None) -> int:
        """Подсчет количества профилей"""
        query = "SELECT COUNT(*) FROM profiles WHERE 1=1"
        params = []
        
        if filters:
            if 'group' in filters:
                query += " AND group_id = ?"
                params.append(filters['group'])
            if 'status' in filters:
                query += " AND status = ?"
                params.append(filters['status'])
        
        cursor = self._connection.execute(query, params)
        return cursor.fetchone()[0]
    
    # === ГРУППЫ ПРОФИЛЕЙ ===
    
    async def save_profile_group(self, group: ProfileGroup):
        """Сохранение группы профилей"""
        self._connection.execute("""
            INSERT OR REPLACE INTO profile_groups (
                id, name, description, profile_count, created_at
            ) VALUES (?, ?, ?, ?, ?)
        """, (
            group.id, group.name, group.description,
            group.profile_count, group.created_at.isoformat()
        ))
        self._connection.commit()
        logger.debug(f"Группа {group.name} сохранена в БД")
    
    async def list_profile_groups(self) -> List[ProfileGroup]:
        """Получение списка всех групп"""
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
                id=row['id'],
                name=row['name'],
                description=row['description'],
                profile_count=row['actual_count'],
                created_at=datetime.fromisoformat(row['created_at'])
            )
            groups.append(group)
        
        return groups
    
    async def delete_profile_group(self, group_id: str) -> bool:
        """Удаление группы профилей"""
        # Обнуляем group_id у профилей в этой группе
        self._connection.execute(
            "UPDATE profiles SET group_id = NULL WHERE group_id = ?", 
            (group_id,)
        )
        
        # Удаляем группу
        cursor = self._connection.execute(
            "DELETE FROM profile_groups WHERE id = ?", (group_id,)
        )
        self._connection.commit()
        
        return cursor.rowcount > 0
    
    # === СТАТИСТИКА ===
    
    async def log_usage(self, usage_stats: UsageStats):
        """Логирование статистики использования"""
        self._connection.execute("""
            INSERT INTO usage_stats (
                profile_id, action, timestamp, duration, success, details
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, (
            usage_stats.profile_id, usage_stats.action,
            usage_stats.timestamp.isoformat(), usage_stats.duration,
            usage_stats.success,
            json.dumps(usage_stats.details) if usage_stats.details else None
        ))
        self._connection.commit()
    
    async def get_profile_usage_stats(self, profile_id: str,
                                    limit: int = 100) -> List[UsageStats]:
        """Получение статистики использования профиля"""
        cursor = self._connection.execute("""
            SELECT * FROM usage_stats 
            WHERE profile_id = ? 
            ORDER BY timestamp DESC 
            LIMIT ?
        """, (profile_id, limit))
        
        rows = cursor.fetchall()
        stats = []
        
        for row in rows:
            stat = UsageStats(
                id=row['id'],
                profile_id=row['profile_id'],
                action=row['action'],
                timestamp=datetime.fromisoformat(row['timestamp']),
                duration=row['duration'],
                success=bool(row['success']),
                details=json.loads(row['details']) if row['details'] else None
            )
            stats.append(stat)
        
        return stats
    
    # === УТИЛИТЫ ===
    
    def _row_to_profile(self, row) -> Profile:
        """Преобразование строки БД в объект Profile"""
        from .models import BrowserSettings, ProxyConfig
        
        # Парсим browser_settings
        browser_settings_data = json.loads(row['browser_settings'])
        browser_settings = BrowserSettings(**browser_settings_data)
        
        # Парсим proxy_config если есть
        proxy = None
        if row['proxy_config']:
            proxy_data = json.loads(row['proxy_config'])
            proxy = ProxyConfig(**proxy_data)
        
        # Парсим extensions
        extensions = json.loads(row['extensions']) if row['extensions'] else []
        
        return Profile(
            id=row['id'],
            name=row['name'],
            group=row['group_id'],
            status=ProfileStatus(row['status']),
            browser_settings=browser_settings,
            proxy=proxy,
            extensions=extensions,
            storage_path=row['storage_path'],
            notes=row['notes'],
            created_at=datetime.fromisoformat(row['created_at']),
            updated_at=datetime.fromisoformat(row['updated_at']),
            last_used=datetime.fromisoformat(row['last_used']) if row['last_used'] else None
        )
    
    async def close(self):
        """Закрытие подключения к базе данных"""
        if self._connection:
            self._connection.close()
            self._connection = None
        logger.info("Подключение к БД закрыто")


class StorageManager:
    """Обновленный StorageManager с полноценной базой данных"""
    
    def __init__(self, db_path: str = "data/profiles.db"):
        self.db = DatabaseManager(db_path)
        logger.info(f"Инициализирован StorageManager с БД: {db_path}")
    
    async def initialize(self):
        """Инициализация базы данных"""
        await self.db.initialize()
    
    # Методы для профилей
    async def save_profile(self, profile: Profile):
        await self.db.save_profile(profile)
    
    async def get_profile(self, profile_id: str) -> Optional[Profile]:
        return await self.db.get_profile(profile_id)
    
    async def update_profile(self, profile: Profile):
        await self.db.update_profile(profile)
    
    async def delete_profile(self, profile_id: str) -> bool:
        return await self.db.delete_profile(profile_id)
    
    async def list_profiles(self, filters: Optional[Dict] = None,
                          limit: Optional[int] = None, offset: int = 0) -> List[Profile]:
        return await self.db.list_profiles(filters, limit, offset)
    
    async def count_profiles(self, filters: Optional[Dict] = None) -> int:
        return await self.db.count_profiles(filters)
    
    # Методы для групп
    async def save_profile_group(self, group: ProfileGroup):
        await self.db.save_profile_group(group)
    
    async def list_profile_groups(self) -> List[ProfileGroup]:
        return await self.db.list_profile_groups()
    
    # Методы для статистики
    async def log_usage(self, usage_stats: UsageStats):
        await self.db.log_usage(usage_stats)
    
    async def get_profile_usage_stats(self, profile_id: str) -> List[UsageStats]:
        return await self.db.get_profile_usage_stats(profile_id)
    
    async def delete_profile_group(self, group_id: str) -> bool:
        return await self.db.delete_profile_group(group_id)
    
    async def close(self):
        """Закрытие базы данных"""
        await self.db.close() 