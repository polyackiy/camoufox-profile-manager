"""Browser profile manager."""

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from .browser_session import BrowserSessionManager
from .database import StorageManager
from .fingerprint_generator import FingerprintGenerator
from .models import BrowserSettings, Profile, ProfileGroup, ProfileStatus, UsageStats


class ProfileManager:
    """Manage browser profiles: CRUD, cloning, export/import, groups."""

    def __init__(self, storage_manager: StorageManager, data_dir: str = "data"):
        self.storage = storage_manager
        self.data_dir = Path(data_dir)
        self.profiles_dir = self.data_dir / "profiles"
        self.fingerprint_generator = FingerprintGenerator()

        # Active browser sessions are owned by a dedicated manager.
        self.browser_sessions = BrowserSessionManager()

        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Initialized ProfileManager with data directory: {self.data_dir}")

    async def initialize(self):
        """Initialize the profile manager and its database."""
        await self.storage.initialize()
        logger.info("ProfileManager initialized")

    async def create_profile(
        self,
        name: str,
        group: str | None = None,
        browser_settings: dict[str, Any] | BrowserSettings | None = None,
        proxy_config: dict[str, Any] | None = None,
        generate_fingerprint: bool = True,
    ) -> Profile:
        """Create a new profile."""
        logger.info(f"Creating new profile: {name}")

        # Create the base profile
        profile = Profile(
            name=name, group=group, notes=f"Created {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        # Generate a browser fingerprint if requested
        if generate_fingerprint:
            fingerprint = await self.fingerprint_generator.generate_fingerprint(browser_settings)
            profile.browser_settings = fingerprint

        # Apply user-provided browser settings
        if browser_settings:
            # If browser_settings is a BrowserSettings object, use it directly
            if isinstance(browser_settings, BrowserSettings):
                profile.browser_settings = browser_settings
            # If it is a dict, apply the fields
            elif isinstance(browser_settings, dict):
                for key, value in browser_settings.items():
                    if hasattr(profile.browser_settings, key):
                        setattr(profile.browser_settings, key, value)

        # Configure the proxy if provided
        if proxy_config:
            from .models import ProxyConfig

            profile.proxy = ProxyConfig(**proxy_config)

        # Create the profile data directory
        profile_dir = Path(profile.get_storage_path(str(self.profiles_dir)))
        profile_dir.mkdir(parents=True, exist_ok=True)

        # Save the profile to the database
        await self.storage.save_profile(profile)

        # Log profile creation
        await self.storage.log_usage(
            UsageStats(
                profile_id=profile.id,
                action="create_profile",
                details={"name": name, "group": group},
            )
        )

        logger.info(f"Profile '{name}' created with ID: {profile.id}")
        return profile

    async def get_profile(self, profile_id: str) -> Profile | None:
        """Get a profile by ID."""
        profile = await self.storage.get_profile(profile_id)
        if profile:
            logger.debug(f"Loaded profile: {profile.name} ({profile_id})")
        else:
            logger.warning(f"Profile with ID {profile_id} not found")
        return profile

    async def update_profile(self, profile_id: str, updates: dict[str, Any]) -> Profile | None:
        """Update a profile."""
        logger.info(f"Updating profile {profile_id}")

        profile = await self.get_profile(profile_id)
        if not profile:
            return None

        # Update profile fields
        for key, value in updates.items():
            # Map proxy_config -> proxy for API compatibility
            if key == "proxy_config":
                key = "proxy"

            if hasattr(profile, key):
                # Coerce status string to enum
                if key == "status" and isinstance(value, str):
                    value = ProfileStatus(value)
                # Coerce proxy dict to ProxyConfig
                elif key == "proxy" and value is not None:
                    if isinstance(value, dict):
                        from camoufox_pm.core.models import ProxyConfig

                        value = ProxyConfig(**value)
                elif key == "proxy" and value is None:
                    value = None
                # Coerce browser_settings dict to BrowserSettings
                elif key == "browser_settings" and value is not None:
                    if isinstance(value, dict):
                        from camoufox_pm.core.models import BrowserSettings, WebRTCMode

                        # Coerce webrtc_mode string to enum
                        if "webrtc_mode" in value and isinstance(value["webrtc_mode"], str):
                            try:
                                value["webrtc_mode"] = WebRTCMode(value["webrtc_mode"])
                            except ValueError:
                                value["webrtc_mode"] = WebRTCMode.REPLACE
                        value = BrowserSettings(**value)
                setattr(profile, key, value)

        profile.updated_at = datetime.now()

        # Persist the update
        await self.storage.update_profile(profile)

        # Log the update
        await self.storage.log_usage(
            UsageStats(
                profile_id=profile_id,
                action="update_profile",
                details={"updated_fields": list(updates.keys())},
            )
        )

        logger.info(f"Profile {profile_id} updated")
        return profile

    async def delete_profile(self, profile_id: str, remove_data: bool = True) -> bool:
        """Delete a profile."""
        logger.info(f"Deleting profile {profile_id}")

        profile = await self.get_profile(profile_id)
        if not profile:
            return False

        # Remove the profile data directory if requested
        if remove_data and profile.storage_path:
            profile_path = Path(profile.storage_path)
            if profile_path.exists():
                shutil.rmtree(profile_path)
                logger.debug(f"Removed profile directory: {profile_path}")

        # Remove from the database
        success = await self.storage.delete_profile(profile_id)

        if success:
            # Log the deletion
            await self.storage.log_usage(
                UsageStats(
                    profile_id=profile_id,
                    action="delete_profile",
                    details={"name": profile.name, "data_removed": remove_data},
                )
            )
            logger.info(f"Profile {profile_id} deleted")
        else:
            logger.error(f"Failed to delete profile {profile_id}")

        return success

    async def list_profiles(
        self,
        group: str | None = None,
        status: ProfileStatus | None = None,
        filters: dict[str, Any] | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Profile]:
        """List profiles with optional filtering."""
        # Merge the legacy parameters with the filters dict
        combined_filters = {}
        if group:
            combined_filters["group"] = group
        if status:
            combined_filters["status"] = status
        if filters:
            combined_filters.update(filters)

        profiles = await self.storage.list_profiles(combined_filters, limit, offset)
        logger.debug(f"Loaded {len(profiles)} profiles")
        return profiles

    async def clone_profile(
        self, source_id: str, new_name: str, regenerate_fingerprint: bool = True
    ) -> Profile | None:
        """Clone a profile."""
        logger.info(f"Cloning profile {source_id} -> {new_name}")

        source_profile = await self.get_profile(source_id)
        if not source_profile:
            return None

        # Create a copy of the profile
        profile_data = source_profile.model_dump()
        profile_data.pop("id")  # Drop the ID so a new one is generated
        profile_data["name"] = new_name
        profile_data["created_at"] = datetime.now()
        profile_data["updated_at"] = datetime.now()
        profile_data["last_used"] = None
        profile_data["storage_path"] = None

        new_profile = Profile(**profile_data)

        # Generate a new fingerprint if requested
        if regenerate_fingerprint:
            fingerprint = await self.fingerprint_generator.generate_fingerprint()
            new_profile.browser_settings = fingerprint

        # Create the directory for the new profile
        profile_dir = Path(new_profile.get_storage_path(str(self.profiles_dir)))
        profile_dir.mkdir(parents=True, exist_ok=True)

        # Copy the source profile data if it exists
        source_path = Path(source_profile.get_storage_path(str(self.profiles_dir)))
        if source_path.exists():
            try:
                shutil.copytree(source_path, profile_dir, dirs_exist_ok=True)
                logger.debug(f"Copied profile data from {source_path} to {profile_dir}")
            except Exception as e:
                logger.warning(f"Failed to copy profile data: {e}")

        # Save the new profile
        await self.storage.save_profile(new_profile)

        # Log the clone
        await self.storage.log_usage(
            UsageStats(
                profile_id=new_profile.id,
                action="clone_profile",
                details={"source_id": source_id, "new_name": new_name},
            )
        )

        logger.info(f"Profile cloned: {new_profile.id}")
        return new_profile

    async def rotate_profile_fingerprint(self, profile_id: str) -> Profile | None:
        """Rotate a profile's fingerprint."""
        logger.info(f"Rotating fingerprint for profile {profile_id}")

        profile = await self.get_profile(profile_id)
        if not profile:
            return None

        # Generate a new fingerprint
        new_fingerprint = await self.fingerprint_generator.generate_fingerprint()
        profile.browser_settings = new_fingerprint
        profile.updated_at = datetime.now()

        # Persist the change
        await self.storage.update_profile(profile)

        # Log the rotation
        await self.storage.log_usage(
            UsageStats(
                profile_id=profile_id,
                action="rotate_fingerprint",
                details={
                    "new_ua": new_fingerprint.user_agent[:50] + "..."
                    if new_fingerprint.user_agent
                    else None
                },
            )
        )

        logger.info(f"Fingerprint for profile {profile_id} rotated")
        return profile

    async def export_profile(self, profile_id: str) -> bytes | None:
        """Export a profile to JSON."""
        profile = await self.get_profile(profile_id)
        if not profile:
            return None

        # Serialize the profile, converting datetimes to ISO strings
        profile_dict = profile.model_dump()
        for key, value in profile_dict.items():
            if isinstance(value, datetime):
                profile_dict[key] = value.isoformat()

        export_data = {
            "profile": profile_dict,
            "exported_at": datetime.now().isoformat(),
            "version": "1.0",
        }

        logger.info(f"Exporting profile {profile_id}")
        return json.dumps(export_data, indent=2, ensure_ascii=False).encode("utf-8")

    async def import_profile(self, data: bytes, new_name: str | None = None) -> Profile | None:
        """Import a profile from JSON."""
        try:
            import_data = json.loads(data.decode("utf-8"))
            profile_data = import_data["profile"]

            # Assign a fresh ID to the imported profile
            profile_data.pop("id", None)
            if new_name:
                profile_data["name"] = new_name

            profile = Profile(**profile_data)

            # Create the directory
            profile_dir = Path(profile.get_storage_path(str(self.profiles_dir)))
            profile_dir.mkdir(parents=True, exist_ok=True)

            # Save the profile
            await self.storage.save_profile(profile)

            logger.info(f"Profile imported: {profile.id}")
            return profile

        except Exception as e:
            logger.error(f"Failed to import profile: {e}")
            return None

    async def get_profile_stats(self, profile_id: str) -> dict[str, Any]:
        """Get usage statistics for a profile."""
        profile = await self.get_profile(profile_id)
        if not profile:
            return {}

        stats = await self.storage.get_profile_usage_stats(profile_id)

        return {
            "profile_id": profile_id,
            "name": profile.name,
            "status": profile.status,
            "created_at": profile.created_at,
            "last_used": profile.last_used,
            "total_sessions": len([s for s in stats if s.action == "launch_browser"]),
            "total_usage_time": sum([s.duration or 0 for s in stats]),
            "success_rate": len([s for s in stats if s.success]) / len(stats) if stats else 0,
        }

    async def bulk_update_profiles(self, profile_ids: list[str], updates: dict[str, Any]) -> int:
        """Update many profiles at once."""
        logger.info(f"Bulk-updating {len(profile_ids)} profiles")

        updated_count = 0
        for profile_id in profile_ids:
            try:
                result = await self.update_profile(profile_id, updates)
                if result:
                    updated_count += 1
            except Exception as e:
                logger.error(f"Failed to update profile {profile_id}: {e}")

        logger.info(f"Updated {updated_count} of {len(profile_ids)} profiles")
        return updated_count

    # Profile group operations

    async def create_group(self, name: str, description: str | None = None) -> dict[str, Any]:
        """Create a new profile group."""
        logger.info(f"Creating new group: {name}")

        group = ProfileGroup(name=name, description=description)

        await self.storage.save_profile_group(group)

        logger.info(f"Group '{name}' created with ID: {group.id}")
        return {
            "id": group.id,
            "name": group.name,
            "description": group.description,
            "created_at": group.created_at,
        }

    async def get_group(self, group_id: str) -> dict[str, Any] | None:
        """Get a group by ID."""
        groups = await self.storage.list_profile_groups()
        group = next((g for g in groups if g.id == group_id), None)
        if not group:
            return None

        # Count the profiles in the group
        profiles = await self.list_profiles(filters={"group": group_id})

        return {
            "id": group.id,
            "name": group.name,
            "description": group.description,
            "profile_count": len(profiles),
            "created_at": group.created_at,
        }

    async def list_groups(self) -> list[dict[str, Any]]:
        """List all groups."""
        groups = await self.storage.list_profile_groups()

        # Include the profile count for each group
        result = []
        for group in groups:
            profiles = await self.list_profiles(filters={"group": group.id})
            result.append(
                {
                    "id": group.id,
                    "name": group.name,
                    "description": group.description,
                    "profile_count": len(profiles),
                    "created_at": group.created_at,
                }
            )

        return result

    async def update_group(self, group_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        """Update a group."""
        logger.info(f"Updating group {group_id}")

        groups = await self.storage.list_profile_groups()
        group = next((g for g in groups if g.id == group_id), None)
        if not group:
            return None

        # Update the group fields
        for key, value in updates.items():
            if hasattr(group, key):
                setattr(group, key, value)

        await self.storage.save_profile_group(group)

        # Count the profiles in the group
        profiles = await self.list_profiles(filters={"group": group_id})

        logger.info(f"Group {group_id} updated")
        return {
            "id": group.id,
            "name": group.name,
            "description": group.description,
            "profile_count": len(profiles),
            "created_at": group.created_at,
        }

    async def delete_group(self, group_id: str) -> bool:
        """Delete a group."""
        logger.info(f"Deleting group {group_id}")

        # Verify the group exists
        groups = await self.storage.list_profile_groups()
        group = next((g for g in groups if g.id == group_id), None)
        if not group:
            return False

        # Ungroup the profiles that belonged to this group
        profiles = await self.list_profiles(filters={"group": group_id})
        for profile in profiles:
            await self.update_profile(profile.id, {"group": None})

        # Delete the group
        success = await self.storage.delete_profile_group(group_id)

        if success:
            logger.info(f"Group {group_id} deleted")
        else:
            logger.error(f"Failed to delete group {group_id}")

        return success

    # -- Browser control (delegated to BrowserSessionManager) ---------------

    async def launch_browser(
        self,
        profile_id: str,
        headless: bool = False,
        window_size: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Launch a Camoufox browser for a profile."""
        profile = await self.get_profile(profile_id)
        if not profile:
            raise ValueError(f"Profile with ID {profile_id} not found")

        if self.browser_sessions.is_running(profile_id):
            session = self.browser_sessions.active_sessions[profile_id]
            return {
                "status": "already_running",
                "profile_id": profile_id,
                "message": "Browser is already running for this profile",
                "process_id": session.process_id,
            }

        await self.update_profile(profile_id, {"last_used": datetime.now()})

        options = profile.to_camoufox_launch_options()
        options["headless"] = headless
        if window_size:
            # Camoufox expects a (width, height) tuple, not a "1280x720" string.
            try:
                width, height = (int(part) for part in window_size.lower().split("x"))
                options["window"] = (width, height)
            except ValueError:
                logger.warning(f"Ignoring invalid window_size {window_size!r} (expected WxH)")
        options.update(kwargs)

        await self.storage.log_usage(
            UsageStats(
                profile_id=profile_id, action="launch_browser", details={"headless": headless}
            )
        )

        session = await self.browser_sessions.launch(
            profile_id, options, on_exit=self._on_browser_exit
        )
        return {
            "status": "launched",
            "profile_id": profile_id,
            "message": "Browser launched successfully",
            "process_id": session.process_id,
            "camoufox_options": {"process_id": session.process_id, "options": options},
        }

    async def _on_browser_exit(self, profile_id: str) -> None:
        """Record usage when a browser exits on its own (e.g. the user closes it)."""
        try:
            await self.storage.log_usage(
                UsageStats(profile_id=profile_id, action="close_browser", details={"forced": False})
            )
        except Exception as exc:  # noqa: BLE001 - logging must never break teardown
            logger.warning(f"Failed to log browser exit for {profile_id}: {exc}")

    async def close_browser(self, profile_id: str) -> dict[str, Any]:
        """Close the browser running for a profile."""
        closed = await self.browser_sessions.close(profile_id)
        if not closed:
            return {
                "status": "not_running",
                "profile_id": profile_id,
                "message": "Browser is not running for this profile",
            }
        await self.storage.log_usage(
            UsageStats(profile_id=profile_id, action="close_browser", details={"forced": True})
        )
        return {
            "status": "closed",
            "profile_id": profile_id,
            "message": "Browser closed successfully",
        }

    async def get_active_browsers(self) -> list[dict[str, Any]]:
        """Return summaries of the browsers currently running."""
        return self.browser_sessions.list_active()

    async def close_all_browsers(self) -> dict[str, Any]:
        """Close every active browser."""
        count = await self.browser_sessions.close_all()
        return {
            "status": "completed",
            "closed_count": count,
            "errors": [],
            "message": f"Closed {count} browsers",
        }
