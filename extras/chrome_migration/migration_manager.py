"""
Migrate Chrome profiles into Camoufox.
"""

import fnmatch
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from camoufox_pm.core.models import Profile
from camoufox_pm.core.profile_manager import ProfileManager

from .importer import ChromeProfileImporter


class ChromeMigrationManager:
    """Automate migration of Chrome profiles into Camoufox."""

    def __init__(
        self,
        profile_manager: ProfileManager,
        config_path: str = "config/chrome_migration_config.yaml",
    ):
        self.profile_manager = profile_manager
        self.chrome_importer = ChromeProfileImporter()
        self.config_path = config_path
        self.config = self._load_config()

    def _load_config(self) -> dict[str, Any]:
        """Load the migration configuration."""
        try:
            config_path = Path(self.config_path)
            if not config_path.exists():
                logger.warning(f"Configuration file not found: {config_path}")
                return self._get_default_config()

            with open(config_path, encoding="utf-8") as f:
                config = yaml.safe_load(f)
                logger.info(f"Loaded migration configuration: {config_path}")
                return config

        except Exception as e:
            logger.error(f"Failed to load configuration: {e}")
            return self._get_default_config()

    def _get_default_config(self) -> dict[str, Any]:
        """Return the default configuration."""
        return {
            "chrome_data_path": None,
            "migration_settings": {
                "include_cookies": True,
                "include_bookmarks": True,
                "include_history": False,
                "include_extensions": False,
                "include_passwords": False,
            },
            "mapping_strategies": {
                "default": {
                    "action": "create_new",
                    "group": "chrome_imports",
                    "name_template": "Chrome - {display_name}",
                    "generate_fingerprint": True,
                }
            },
            "profile_mapping": [],
            "security_settings": {
                "backup_chrome_data": True,
                "verify_data_integrity": True,
                "log_migration_details": True,
            },
            "exclusion_filters": {
                "exclude_cookie_domains": [],
                "exclude_cookie_names": [],
                "exclude_history_domains": [],
            },
        }

    async def discover_chrome_profiles(
        self, chrome_data_path: str | None = None
    ) -> list[dict[str, Any]]:
        """Discover all Chrome profiles."""
        if not chrome_data_path:
            chrome_data_path = self.config.get("chrome_data_path")

        chrome_profiles = self.chrome_importer.find_chrome_profiles(chrome_data_path)

        # Enrich the profile information
        enriched_profiles = []
        for profile in chrome_profiles:
            enriched_profile = profile.copy()
            enriched_profile["migration_status"] = "not_migrated"
            enriched_profile["suggested_mapping"] = await self._suggest_mapping(profile)
            enriched_profiles.append(enriched_profile)

        logger.info(f"Discovered {len(enriched_profiles)} Chrome profiles")
        return enriched_profiles

    async def _suggest_mapping(self, chrome_profile: dict[str, Any]) -> dict[str, Any]:
        """Suggest a mapping for a Chrome profile."""
        chrome_name = chrome_profile["name"]
        display_name = chrome_profile["display_name"]

        # Look for an exact match in the configuration
        for mapping in self.config.get("profile_mapping", []):
            if mapping.get("chrome_profile") == chrome_name:
                return {"type": "configured", "mapping": mapping}

            if mapping.get("chrome_display_name") == display_name:
                return {"type": "configured", "mapping": mapping}

            # Check the patterns
            if mapping.get("chrome_profile_pattern"):
                if fnmatch.fnmatch(chrome_name, mapping["chrome_profile_pattern"]):
                    return {"type": "pattern_match", "mapping": mapping}

        # Check existing Camoufox profiles with similar names
        existing_profiles = await self.profile_manager.list_profiles()
        for existing_profile in existing_profiles:
            if (
                display_name.lower() in existing_profile.name.lower()
                or existing_profile.name.lower() in display_name.lower()
            ):
                return {
                    "type": "name_similarity",
                    "camoufox_profile_id": existing_profile.id,
                    "camoufox_profile_name": existing_profile.name,
                    "confidence": 0.8,
                }

        # Suggest automatic creation
        return {
            "type": "auto_create",
            "suggested_name": f"Chrome - {display_name}",
            "suggested_group": self.config["mapping_strategies"]["default"]["group"],
        }

    async def migrate_profile(
        self, chrome_profile: dict[str, Any], mapping: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Migrate a single Chrome profile."""
        logger.info(f"Starting migration of profile: {chrome_profile['display_name']}")

        migration_result = {
            "chrome_profile": chrome_profile["name"],
            "chrome_display_name": chrome_profile["display_name"],
            "success": False,
            "camoufox_profile_id": None,
            "camoufox_profile_name": None,
            "migration_details": {},
            "errors": [],
            "started_at": datetime.now().isoformat(),
        }

        try:
            # Resolve the mapping
            if not mapping:
                suggested = await self._suggest_mapping(chrome_profile)
                if suggested["type"] == "configured":
                    mapping = suggested["mapping"]
                else:
                    # Use automatic creation
                    # Build a unique profile name
                    if (
                        chrome_profile["display_name"]
                        and chrome_profile["display_name"] != chrome_profile["name"]
                    ):
                        default_name = (
                            f"Chrome - {chrome_profile['display_name']} ({chrome_profile['name']})"
                        )
                    else:
                        default_name = f"Chrome - {chrome_profile['name']}"

                    mapping = {
                        "create_new_profile": True,
                        "new_profile_name": suggested.get("suggested_name", default_name),
                        "new_profile_group": suggested.get("suggested_group", "chrome_imports"),
                        "migration_settings": self.config["migration_settings"],
                    }

            # Get or create the target Camoufox profile
            camoufox_profile = await self._get_or_create_target_profile(mapping)
            if not camoufox_profile:
                raise RuntimeError("Failed to get or create the target Camoufox profile")

            migration_result["camoufox_profile_id"] = camoufox_profile.id
            migration_result["camoufox_profile_name"] = camoufox_profile.name

            # Read the migration settings
            migration_settings = mapping.get(
                "migration_settings", self.config["migration_settings"]
            )

            # Run the data migration
            import_result = self.chrome_importer.migrate_chrome_profile_to_camoufox(
                chrome_profile["path"],
                camoufox_profile,
                include_cookies=migration_settings.get("include_cookies", True),
                include_bookmarks=migration_settings.get("include_bookmarks", True),
                include_history=migration_settings.get("include_history", False),
            )

            migration_result["migration_details"] = import_result
            migration_result["success"] = import_result["success"]
            migration_result["errors"] = import_result.get("errors", [])

            # Update the profile notes
            await self._update_profile_notes(camoufox_profile, chrome_profile, import_result)

            logger.info(
                f"Profile migration complete: {chrome_profile['display_name']} -> {camoufox_profile.name}"
            )

        except Exception as e:
            error_msg = f"Failed to migrate profile {chrome_profile['display_name']}: {e}"
            logger.error(error_msg)
            migration_result["errors"].append(error_msg)

        migration_result["completed_at"] = datetime.now().isoformat()
        return migration_result

    async def _get_or_create_target_profile(self, mapping: dict[str, Any]) -> Profile | None:
        """Get or create the target Camoufox profile."""
        # If an existing profile is specified
        if mapping.get("camoufox_profile_id"):
            profile = await self.profile_manager.get_profile(mapping["camoufox_profile_id"])
            if profile:
                return profile
            else:
                logger.warning(f"Profile {mapping['camoufox_profile_id']} not found")

        # If a new profile must be created
        if mapping.get("create_new_profile", False):
            profile_name = mapping.get("new_profile_name", "Imported Chrome Profile")
            profile_group = mapping.get("new_profile_group", "chrome_imports")

            # Browser settings for the new profile
            browser_settings = None
            generate_fingerprint = self.config["mapping_strategies"]["default"].get(
                "generate_fingerprint", True
            )

            # Create the profile
            profile = await self.profile_manager.create_profile(
                name=profile_name,
                group=profile_group,
                browser_settings=browser_settings,
                generate_fingerprint=generate_fingerprint,
            )

            return profile

        return None

    async def _update_profile_notes(
        self,
        camoufox_profile: Profile,
        chrome_profile: dict[str, Any],
        migration_result: dict[str, Any],
    ):
        """Append migration details to the profile notes."""
        migration_info = (
            f"Migrated from Chrome profile: {chrome_profile['display_name']}\n"
            f"Source path: {chrome_profile['path']}\n"
            f"Migration date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Cookies: {migration_result.get('cookies_imported', 0)}\n"
            f"Bookmarks: {migration_result.get('bookmarks_imported', 0)}\n"
            f"History: {migration_result.get('history_imported', 0)}\n"
        )

        # Append to the existing notes
        existing_notes = camoufox_profile.notes or ""
        updated_notes = f"{existing_notes}\n\n--- MIGRATED FROM CHROME ---\n{migration_info}"

        # Update the profile
        await self.profile_manager.update_profile(camoufox_profile.id, {"notes": updated_notes})

    async def migrate_all_profiles(
        self, chrome_data_path: str | None = None, dry_run: bool = False
    ) -> dict[str, Any]:
        """Migrate all Chrome profiles per the configuration."""
        logger.info("Starting bulk migration of Chrome profiles")

        results = {
            "started_at": datetime.now().isoformat(),
            "chrome_profiles_found": 0,
            "profiles_migrated": 0,
            "profiles_failed": 0,
            "migration_results": [],
            "errors": [],
            "dry_run": dry_run,
        }

        try:
            # Discover Chrome profiles
            chrome_profiles = await self.discover_chrome_profiles(chrome_data_path)
            results["chrome_profiles_found"] = len(chrome_profiles)

            if dry_run:
                logger.info("DRY RUN: no migration will be performed")
                for chrome_profile in chrome_profiles:
                    suggested = await self._suggest_mapping(chrome_profile)
                    results["migration_results"].append(
                        {
                            "chrome_profile": chrome_profile["name"],
                            "chrome_display_name": chrome_profile["display_name"],
                            "suggested_mapping": suggested,
                            "would_migrate": True,
                        }
                    )
                return results

            # Migrate each profile
            for chrome_profile in chrome_profiles:
                try:
                    migration_result = await self.migrate_profile(chrome_profile)
                    results["migration_results"].append(migration_result)

                    if migration_result["success"]:
                        results["profiles_migrated"] += 1
                    else:
                        results["profiles_failed"] += 1

                except Exception as e:
                    error_msg = f"Fatal migration error for {chrome_profile['display_name']}: {e}"
                    logger.error(error_msg)
                    results["errors"].append(error_msg)
                    results["profiles_failed"] += 1

        except Exception as e:
            error_msg = f"Fatal bulk-migration error: {e}"
            logger.error(error_msg)
            results["errors"].append(error_msg)

        results["completed_at"] = datetime.now().isoformat()

        # Log the totals
        logger.info(
            f"Migration complete: {results['profiles_migrated']} succeeded, "
            f"{results['profiles_failed']} failed of {results['chrome_profiles_found']}"
        )

        return results

    async def generate_mapping_template(
        self,
        chrome_data_path: str | None = None,
        output_path: str = "chrome_migration_mapping.yaml",
    ) -> str:
        """Generate a mapping template from the discovered profiles."""
        chrome_profiles = await self.discover_chrome_profiles(chrome_data_path)
        camoufox_profiles = await self.profile_manager.list_profiles()

        template = {
            "# Auto-generated profile mapping template": None,
            "# Created": datetime.now().isoformat(),
            "chrome_data_path": chrome_data_path,
            "profile_mapping": [],
        }

        for chrome_profile in chrome_profiles:
            # Build a unique profile name for the template
            if (
                chrome_profile["display_name"]
                and chrome_profile["display_name"] != chrome_profile["name"]
            ):
                suggested_name = (
                    f"Chrome - {chrome_profile['display_name']} ({chrome_profile['name']})"
                )
            else:
                suggested_name = f"Chrome - {chrome_profile['name']}"

            mapping_entry = {
                f"# Chrome profile: {chrome_profile['display_name']}": None,
                "chrome_profile": chrome_profile["name"],
                "chrome_display_name": chrome_profile["display_name"],
                "chrome_path": chrome_profile["path"],
                "# Choose one of the options below:": None,
                "# Option 1: migrate into an existing profile": None,
                "camoufox_profile_id": "# set the profile ID",
                "# Option 2: create a new profile": None,
                "create_new_profile": False,
                "new_profile_name": suggested_name,
                "new_profile_group": "chrome_imports",
                "migration_settings": {
                    "include_cookies": True,
                    "include_bookmarks": True,
                    "include_history": False,
                },
            }
            template["profile_mapping"].append(mapping_entry)

        # Add information about existing Camoufox profiles
        template["# Existing Camoufox profiles for reference:"] = None
        template["existing_camoufox_profiles"] = []

        for profile in camoufox_profiles:
            template["existing_camoufox_profiles"].append(
                {
                    "id": profile.id,
                    "name": profile.name,
                    "group": profile.group,
                    "status": profile.status,
                }
            )

        # Save the template
        with open(output_path, "w", encoding="utf-8") as f:
            yaml.dump(template, f, allow_unicode=True, default_flow_style=False)

        logger.info(f"Mapping template saved: {output_path}")
        return output_path

    def save_config(self, config_path: str | None = None):
        """Save the current configuration."""
        if not config_path:
            config_path = self.config_path

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(self.config, f, allow_unicode=True, default_flow_style=False)

        logger.info(f"Configuration saved: {config_path}")

    async def get_migration_status(self) -> dict[str, Any]:
        """Return the migration status."""
        chrome_profiles = await self.discover_chrome_profiles()
        camoufox_profiles = await self.profile_manager.list_profiles()

        # Determine which profiles are already migrated
        migrated_profiles = []
        for profile in camoufox_profiles:
            if profile.notes and "MIGRATED FROM CHROME" in profile.notes:
                migrated_profiles.append(profile)

        return {
            "chrome_profiles_found": len(chrome_profiles),
            "camoufox_profiles_total": len(camoufox_profiles),
            "migrated_profiles": len(migrated_profiles),
            "chrome_profiles": chrome_profiles,
            "migrated_profile_details": [
                {
                    "id": p.id,
                    "name": p.name,
                    "group": p.group,
                    "created_at": p.created_at.isoformat(),
                }
                for p in migrated_profiles
            ],
        }
