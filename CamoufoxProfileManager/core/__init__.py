"""
Ядро системы управления профилями
"""

from .models import Profile, ProfileGroup, ProxyConfig, BrowserSettings
from .profile_manager import ProfileManager

__all__ = ['Profile', 'ProfileGroup', 'ProxyConfig', 'BrowserSettings', 'ProfileManager'] 