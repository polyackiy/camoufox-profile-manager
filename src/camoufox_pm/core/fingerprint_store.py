"""Freeze a profile's fingerprint so it looks like the same machine every time.

Camoufox generates a fresh fingerprint on every launch. That is the right default
for a privacy tool, but it is the opposite of what a profile manager needs: an
account opened from a machine with 12 cores and an NVIDIA GPU should not come
back tomorrow with 32 cores and an AMD GPU.

So the first launch resolves the fingerprint once, and it is stored and replayed
from then on. Camoufox fills a config key only when it is absent (``set_into`` /
``merge_into``), so a stored value always wins over a freshly generated one.

Not everything is frozen. A profile's hardware is an identity and must never
drift; its geolocation, timezone and WebRTC address describe *where it is
connecting from* and must follow the proxy in use. Freezing those would pin a
Berlin timezone onto a profile that later moves to a Tokyo proxy — a mismatch
that is trivial to detect and worse than no spoofing at all.
"""

import json
from typing import Any

from loguru import logger

# Prefixes whose values describe the machine. These are frozen for the life of
# the profile: navigator, screen/window geometry, GPU, and the noise seeds that
# drive canvas/audio/font rendering.
_FROZEN_PREFIXES = (
    "navigator.",
    "screen.",
    "window.",
    "webGl:",
    "webGl2:",
    "webGl.",
    "webGl2.",
    "canvas:",
    "audio:",
    "fonts",
    "mediaDevices:",
    "voices",
    "AudioContext:",
)

# Never frozen, even though they match a prefix above:
#  - navigator.language(s) follow the profile's configured languages.
#  - window.history.length is session state, not hardware; a real profile's
#    history grows, so let Camoufox keep varying it.
#  - addons are absolute paths on the machine that generated them.
_NEVER_FROZEN = frozenset(
    {
        "navigator.language",
        "navigator.languages",
        "window.history.length",
        "addons",
    }
)


def freeze(resolved: dict[str, Any]) -> dict[str, Any]:
    """Return the subset of a resolved Camoufox config that identifies the machine.

    Everything else — geolocation, timezone, locale, WebRTC, request headers —
    is left out so it keeps tracking the profile's settings and its proxy.
    """
    return {
        key: value
        for key, value in resolved.items()
        if key not in _NEVER_FROZEN and key.startswith(_FROZEN_PREFIXES)
    }


def resolve(launch_options: dict[str, Any]) -> dict[str, Any]:
    """Ask Camoufox to resolve a full fingerprint for these launch constraints.

    Returns the frozen subset, or an empty dict if Camoufox cannot resolve one
    (it is not installed, or its internals moved). An empty result is not fatal:
    the browser still launches, it just keeps generating a fingerprint per run.
    """
    try:
        from camoufox.utils import launch_options as camoufox_launch_options
    except ImportError:  # pragma: no cover - exercised only without camoufox
        logger.warning("Camoufox is not installed; cannot pin a fingerprint")
        return {}

    # launch_options() resolves the fingerprint but knows nothing about the
    # Playwright-level arguments, so those are dropped first.
    constraints = {
        key: value
        for key, value in launch_options.items()
        if key not in ("user_data_dir", "persistent_context")
    }
    constraints["headless"] = True

    try:
        resolved = camoufox_launch_options(**constraints)
        config = json.loads(resolved["env"]["CAMOU_CONFIG_1"])
    except Exception as exc:  # noqa: BLE001 - never block a launch over this
        logger.warning(f"Could not resolve a fingerprint to pin: {exc}")
        return {}

    frozen = freeze(config)
    logger.info(f"Pinned a fingerprint with {len(frozen)} properties")
    return frozen


def summarize(fingerprint: dict[str, Any] | None) -> dict[str, Any] | None:
    """Describe a pinned fingerprint in the few values worth showing a user.

    The stored config is ~40 properties and tens of kilobytes; this is what the
    UI displays so someone can confirm the profile really has a fixed machine.
    """
    if not fingerprint:
        return None

    width = fingerprint.get("screen.width")
    height = fingerprint.get("screen.height")
    fonts = fingerprint.get("fonts")
    return {
        "user_agent": fingerprint.get("navigator.userAgent"),
        "platform": fingerprint.get("navigator.platform"),
        "hardware_concurrency": fingerprint.get("navigator.hardwareConcurrency"),
        "screen": f"{width}x{height}" if width and height else None,
        "gpu": fingerprint.get("webGl:renderer"),
        "font_count": len(fonts) if isinstance(fonts, list) else None,
        "property_count": len(fingerprint),
    }
