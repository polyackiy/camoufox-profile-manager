"""Core of the profile management system."""

from typing import TYPE_CHECKING

from .models import BrowserSettings, Profile, ProfileGroup, ProxyConfig

if TYPE_CHECKING:
    from .profile_manager import ProfileManager

__all__ = ["Profile", "ProfileGroup", "ProxyConfig", "BrowserSettings", "ProfileManager"]


def __getattr__(name: str):
    # Permission/recovery/crypto helpers must not initialize the browser stack.
    # Keep the public convenience import while loading its dependencies on demand.
    if name == "ProfileManager":
        from .profile_manager import ProfileManager

        globals()[name] = ProfileManager
        return ProfileManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
