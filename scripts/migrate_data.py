#!/usr/bin/env python3
"""Copy profiles and groups from demo_data/ into data/.

Run from the repository root:

    uv run python scripts/migrate_data.py
"""

import asyncio
import traceback

from loguru import logger

from camoufox_pm.core.database import StorageManager
from camoufox_pm.core.profile_manager import ProfileManager


async def migrate_data() -> None:
    """Copy all profiles and groups from demo_data into data."""
    logger.info("Migrating data from demo_data to data")

    source = StorageManager("demo_data/profiles.db")
    await source.initialize()
    source_manager = ProfileManager(source, "demo_data")
    await source_manager.initialize()

    target = StorageManager("data/profiles.db")
    await target.initialize()
    target_manager = ProfileManager(target, "data")
    await target_manager.initialize()

    try:
        groups = await source.list_profile_groups()
        for group in groups:
            await target.save_profile_group(group)
        logger.info(f"Migrated {len(groups)} groups")

        profiles = await source_manager.list_profiles()
        for profile in profiles:
            await target.save_profile(profile)
        logger.info(f"Migrated {len(profiles)} profiles")
    except Exception as exc:
        logger.error(f"Migration failed: {exc}")
        logger.error(traceback.format_exc())
    finally:
        await source.close()
        await target.close()


if __name__ == "__main__":
    asyncio.run(migrate_data())
