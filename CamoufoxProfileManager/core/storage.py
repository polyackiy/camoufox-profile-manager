"""
Временная заглушка для StorageManager
"""

from typing import List, Optional, Dict, Any
from .models import Profile, UsageStats


class StorageManager:
    """Временная заглушка для StorageManager"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.profiles = {}
        self.usage_stats = []
    
    async def save_profile(self, profile: Profile):
        self.profiles[profile.id] = profile
    
    async def get_profile(self, profile_id: str) -> Optional[Profile]:
        return self.profiles.get(profile_id)
    
    async def update_profile(self, profile: Profile):
        if profile.id in self.profiles:
            self.profiles[profile.id] = profile
    
    async def delete_profile(self, profile_id: str) -> bool:
        if profile_id in self.profiles:
            del self.profiles[profile_id]
            return True
        return False
    
    async def list_profiles(self, filters: Optional[Dict] = None, 
                          limit: Optional[int] = None, offset: int = 0) -> List[Profile]:
        return list(self.profiles.values())
    
    async def log_usage(self, usage_stats: UsageStats):
        self.usage_stats.append(usage_stats)
    
    async def get_profile_usage_stats(self, profile_id: str) -> List[UsageStats]:
        return [stats for stats in self.usage_stats if stats.profile_id == profile_id] 