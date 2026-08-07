"""
Smoke tests for the Chrome migration module.
"""

import asyncio
import sys
from pathlib import Path

from loguru import logger

# Add the module path
sys.path.append(str(Path(__file__).parent))


async def test_chrome_detection():
    """Test Chrome profile discovery."""
    logger.info("Test: Chrome profile discovery")

    try:
        from .importer import ChromeProfileImporter

        importer = ChromeProfileImporter()

        # Show system information
        logger.info(f"Operating system: {importer.system}")
        logger.info(f"Chrome search paths: {importer.chrome_data_paths}")

        # Find profiles
        profiles = importer.find_chrome_profiles()

        if profiles:
            logger.info(f"Found {len(profiles)} Chrome profiles:")
            for i, profile in enumerate(profiles, 1):
                logger.info(f"  {i}. {profile['display_name']} ({profile['name']})")
                logger.info(f"     Path: {profile['path']}")
                logger.info(f"     Cookies: {'yes' if profile['has_cookies'] else 'no'}")

                # Check that the files exist
                profile_path = Path(profile["path"])
                cookies_path = Path(profile["cookies_path"]) if profile["cookies_path"] else None

                logger.info(f"     Directory exists: {'yes' if profile_path.exists() else 'no'}")
                if cookies_path:
                    logger.info(
                        f"     Cookie file exists: {'yes' if cookies_path.exists() else 'no'}"
                    )
        else:
            logger.warning("No Chrome profiles found")
            logger.info("Possible reasons:")
            logger.info("   - Chrome is not installed")
            logger.info("   - Profiles are stored elsewhere")
            logger.info("   - No access permissions")

        return len(profiles) > 0

    except Exception as e:
        logger.error(f"Chrome discovery test error: {e}")
        return False


async def test_basic_migration():
    """Test basic migration functionality."""
    logger.info("Test: basic migration")

    try:
        from camoufox_pm.core.database import StorageManager
        from camoufox_pm.core.profile_manager import ProfileManager

        from .migration_manager import ChromeMigrationManager

        # Initialization
        storage = StorageManager()
        await storage.initialize()

        profile_manager = ProfileManager(storage)
        await profile_manager.initialize()

        migration_manager = ChromeMigrationManager(profile_manager)

        # Test profile discovery
        chrome_profiles = await migration_manager.discover_chrome_profiles()

        if not chrome_profiles:
            logger.warning("No Chrome profiles found; skipping the migration test")
            return True

        logger.info(f"Found {len(chrome_profiles)} Chrome profiles")

        # Test mapping-template generation
        template_path = await migration_manager.generate_mapping_template(
            output_path="test_chrome_mapping.yaml"
        )
        logger.info(f"Mapping template created: {template_path}")

        # Test migration status
        status = await migration_manager.get_migration_status()
        logger.info("Migration status:")
        logger.info(f"  Chrome profiles: {status['chrome_profiles_found']}")
        logger.info(f"  Camoufox profiles: {status['camoufox_profiles_total']}")
        logger.info(f"  Already migrated: {status['migrated_profiles']}")

        # Test the dry run
        dry_run_result = await migration_manager.migrate_all_profiles(dry_run=True)
        logger.info(
            f"Dry run: {dry_run_result['chrome_profiles_found']} profiles would be processed"
        )

        logger.info("Basic migration test passed")
        return True

    except Exception as e:
        logger.error(f"Basic migration test error: {e}")
        import traceback

        logger.error(traceback.format_exc())
        return False


async def test_config_generation():
    """Test configuration generation."""
    logger.info("Test: configuration generation")

    try:
        # Test loading the default configuration
        from camoufox_pm.core.database import StorageManager
        from camoufox_pm.core.profile_manager import ProfileManager

        from .migration_manager import ChromeMigrationManager

        storage = StorageManager()
        await storage.initialize()
        profile_manager = ProfileManager(storage)
        await profile_manager.initialize()

        # Create a manager with a missing config (should use the default)
        migration_manager = ChromeMigrationManager(
            profile_manager, config_path="nonexistent_config.yaml"
        )

        # Check that the default configuration is loaded
        assert migration_manager.config is not None
        assert "default_migration_settings" in migration_manager.config

        logger.info("Default configuration loaded")

        # Test saving the configuration
        test_config_path = "test_chrome_config.yaml"
        migration_manager.config_path = test_config_path
        migration_manager.save_config()

        # Check that the file was created
        config_file = Path(test_config_path)
        if config_file.exists():
            logger.info(f"Configuration saved: {test_config_path}")

            # Read and verify the contents
            import yaml

            with open(test_config_path, encoding="utf-8") as f:
                saved_config = yaml.safe_load(f)

            assert saved_config is not None
            assert "default_migration_settings" in saved_config

            logger.info("Configuration saved and reloaded correctly")

            # Remove the test file
            config_file.unlink()
        else:
            logger.error("Configuration file was not created")
            return False

        return True

    except Exception as e:
        logger.error(f"Configuration test error: {e}")
        return False


def test_chrome_paths():
    """Test Chrome path resolution."""
    logger.info("Test: Chrome path resolution")

    try:
        from .importer import ChromeProfileImporter

        importer = ChromeProfileImporter()

        # Check that the paths are resolved
        assert importer.chrome_data_paths is not None
        assert "profiles" in importer.chrome_data_paths

        logger.info(f"System: {importer.system}")
        logger.info(f"Profiles path: {importer.chrome_data_paths['profiles']}")

        # Check that the path is correct for the current OS
        profiles_path = importer.chrome_data_paths["profiles"]
        assert profiles_path != ""

        if importer.system == "windows":
            assert "AppData" in profiles_path
        elif importer.system == "darwin":
            assert "Library" in profiles_path
        elif importer.system == "linux":
            assert ".config" in profiles_path

        logger.info("Chrome paths resolved correctly")
        return True

    except Exception as e:
        logger.error(f"Chrome paths test error: {e}")
        return False


async def run_all_tests():
    """Run all tests."""
    logger.info("Running Chrome migration tests")
    logger.info("=" * 50)

    tests = [
        ("Chrome path resolution", test_chrome_paths),
        ("Chrome profile discovery", test_chrome_detection),
        ("Configuration generation", test_config_generation),
        ("Basic migration functionality", test_basic_migration),
    ]

    results = []

    for test_name, test_func in tests:
        logger.info(f"\nRunning: {test_name}")
        try:
            if asyncio.iscoroutinefunction(test_func):
                result = await test_func()
            else:
                result = test_func()
            results.append((test_name, result))
        except Exception as e:
            logger.error(f"Fatal error in test '{test_name}': {e}")
            results.append((test_name, False))

    # Print the totals
    logger.info("\nTest results:")
    logger.info("=" * 50)

    passed = 0
    total = len(results)

    for test_name, result in results:
        if result:
            logger.success(f"✅ {test_name}")
            passed += 1
        else:
            logger.error(f"❌ {test_name}")

    logger.info(f"\nTotal: {passed}/{total} tests passed")

    if passed == total:
        logger.info("All tests passed!")
        logger.info("The Chrome migration system is ready to use")
        logger.info("Run wizard.py for interactive migration")
    else:
        logger.warning(f"{total - passed} tests failed")
        logger.info("Check the logs above to troubleshoot")

    return passed == total


if __name__ == "__main__":
    asyncio.run(run_all_tests())
