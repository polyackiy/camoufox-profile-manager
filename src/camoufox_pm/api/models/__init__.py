"""
API модели для CamoufoxProfileManager
"""

from .profiles import *
from .groups import *
from .system import *

__all__ = [
    # Profile models
    "ProfileCreateRequest",
    "ProfileUpdateRequest", 
    "ProfileResponse",
    "ProfileListResponse",
    "ProfileStatsResponse",
    "ProfileCloneRequest",
    "ProfileLaunchRequest",
    "ProfileLaunchResponse",
    
    # Group models
    "GroupCreateRequest",
    "GroupUpdateRequest",
    "GroupResponse", 
    "GroupListResponse",
    
    # System models
    "SystemStatusResponse",
    "ApiResponse",
    "ErrorResponse",
    "PaginationResponse",
] 