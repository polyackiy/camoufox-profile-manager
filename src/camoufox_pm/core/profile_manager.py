"""Browser profile manager."""

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from . import fingerprint_store, profile_archive
from .browser_session import BrowserSessionManager
from .database import StorageManager
from .fingerprint_generator import FingerprintGenerator
from .models import (
    BrowserSettings,
    Profile,
    ProfileGroup,
    ProfileStatus,
    UsageStats,
    generate_profile_id,
)


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
        notes: str | None = None,
        fingerprint_preset: str | None = None,
    ) -> Profile:
        """Create a new profile.

        ``fingerprint_preset`` is an id from :func:`fingerprint_store.list_presets`.
        When given, the profile is pinned to that captured real device instead of
        waiting for its first launch to generate a synthetic one.
        """
        logger.info(f"Creating new profile: {name}")

        # Notes belong to the user; the creation time is already in created_at,
        # so it is not stamped into the field the user types into.
        profile = Profile(name=name, group=group, notes=notes)

        # Generate a browser fingerprint if requested
        if generate_fingerprint:
            fingerprint = await self.fingerprint_generator.generate_fingerprint(browser_settings)
            profile.browser_settings = fingerprint

        # Apply user-provided browser settings over the generated fingerprint.
        if browser_settings:
            if isinstance(browser_settings, BrowserSettings):
                profile.browser_settings = browser_settings
            elif isinstance(browser_settings, dict):
                # Rebuild through the model rather than setattr, so values are
                # validated and enums coerced (a raw "webrtc_mode" string would
                # otherwise survive as str and trip serialization). Keys the
                # caller omitted keep their generated value; an explicit null
                # clears one, which is how "geolocation from the proxy IP" works.
                merged = profile.browser_settings.model_dump()
                merged.update(browser_settings)
                profile.browser_settings = BrowserSettings(**merged)

        # Configure the proxy if provided
        if proxy_config:
            from .models import ProxyConfig

            profile.proxy = ProxyConfig(**proxy_config)

        # Pin a real device now, if one was chosen. Otherwise the first launch
        # pins a generated fingerprint instead.
        if fingerprint_preset:
            preset = fingerprint_store.get_preset(fingerprint_preset)
            if preset is None:
                raise ValueError(f"Unknown fingerprint preset: {fingerprint_preset}")
            # The catalogue can be read without the browser, but pinning a device
            # cannot. Say so instead of quietly creating the profile and letting
            # its first launch generate a different machine than the one chosen.
            if not fingerprint_store.can_resolve():
                raise ValueError(
                    "The Camoufox browser is required to pin a device preset. "
                    "Run 'camoufox fetch', then create the profile."
                )

            # A preset carries its own OS; keep the profile's setting in step so
            # the two cannot describe different machines.
            preset_os = fingerprint_preset.split(":", 1)[0]
            if preset_os in ("windows", "macos", "linux"):
                profile.browser_settings.os = preset_os

            # The preset *is* the hardware, so generated values must not override
            # it — an explicit hardware_concurrency wins over the preset's own and
            # would give the profile a CPU count the real device never had. Values
            # the caller asked for are still honoured.
            asked_for = set(browser_settings) if isinstance(browser_settings, dict) else set()
            described = fingerprint_store.describe_preset(preset)
            if "hardware_concurrency" not in asked_for:
                profile.browser_settings.hardware_concurrency = None
            if "screen" not in asked_for and described.get("screen"):
                profile.browser_settings.screen = described["screen"]

            profile.fingerprint = fingerprint_store.resolve(
                profile.to_camoufox_launch_options(), preset=preset
            )
            # can_resolve() only rules out a missing browser; resolution can still
            # fail for other reasons. Check the result, not a proxy for it —
            # otherwise the profile is created unpinned and its first launch
            # quietly assigns a generated machine instead of the chosen device.
            if not profile.fingerprint:
                raise ValueError(
                    f"Could not pin the device for preset {fingerprint_preset}. "
                    "The profile was not created; see the server log for the cause."
                )

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
        # Drop the pinned machine too, otherwise the profile would keep the old
        # hardware forever and only its high-level settings would change. The
        # next launch resolves and pins a new one.
        profile.fingerprint = None
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

    # -- Portability --------------------------------------------------------

    async def export_profile(self, profile_id: str, destination: Path) -> Path:
        """Write a profile and its browser data to an archive.

        Refuses while the browser is open: the databases would be copied
        mid-write and the restored profile could come back corrupted.
        """
        profile = await self.get_profile(profile_id)
        if not profile:
            raise ValueError(f"Profile with ID {profile_id} not found")
        if self.browser_sessions.is_running(profile_id):
            raise ValueError("Close the browser before exporting this profile")

        data_dir = Path(profile.get_storage_path(str(self.profiles_dir)))
        profile_archive.export_profile(profile, data_dir, destination)

        await self.storage.log_usage(
            UsageStats(profile_id=profile_id, action="export_profile", details={})
        )
        return destination

    async def import_profile(self, source: Path, name: str | None = None) -> Profile:
        """Create a profile from an archive, with a new id of its own."""
        record = profile_archive.read_archive(source)

        profile = Profile(**{**record, "name": name or record.get("name") or "Imported profile"})
        # A fresh id and storage path: the archive may well be restored next to
        # the profile it came from.
        profile.id = generate_profile_id()
        profile.storage_path = None
        # The group id belongs to the source instance and would dangle here; the
        # user can reassign the profile to a local group.
        profile.group = None
        data_dir = Path(profile.get_storage_path(str(self.profiles_dir)))

        # Extraction writes into the directory before it can fail, and a profile
        # is only real once it is in the database. Without this, every refused
        # import (a bomb, a bad zip, an I/O error) would strand a directory that
        # nothing references and nothing ever deletes.
        try:
            profile_archive.extract_data(source, data_dir)
            await self.storage.save_profile(profile)
        except Exception:
            shutil.rmtree(data_dir, ignore_errors=True)
            raise

        await self.storage.log_usage(
            UsageStats(
                profile_id=profile.id, action="import_profile", details={"name": profile.name}
            )
        )
        logger.info(f"Imported profile '{profile.name}' as {profile.id}")
        return profile

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

        # Pin the machine on the first launch and replay it on every one after,
        # so the profile is the same computer each session instead of new
        # hardware every time. The profile's own overrides still win, and geo,
        # timezone and WebRTC stay dynamic so they follow the proxy.
        pinned = profile.fingerprint
        if not pinned:
            # Deliberately synchronous. There is no await between reading the
            # profile above and writing it below, which is what makes this
            # read-modify-write atomic: save_profile rewrites the whole row, so
            # an await here would let a concurrent rename be silently reverted.
            # resolve() can do network I/O (it fetches the uBlock addon on a
            # fresh install), so offloading it to a thread is tempting — do that
            # only together with row-level concurrency control.
            pinned = fingerprint_store.resolve(options)
            if pinned:
                profile.fingerprint = pinned
        if pinned:
            options["config"] = {**pinned, **options.get("config", {})}

        # One write for both the pin and the timestamp, after the options are
        # built, so neither can be clobbered by a stale copy of the profile.
        profile.last_used = datetime.now()
        profile.updated_at = datetime.now()
        await self.storage.update_profile(profile)

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
