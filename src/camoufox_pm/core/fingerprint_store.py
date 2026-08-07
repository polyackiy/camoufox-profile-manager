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
from functools import lru_cache
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


def resolve(launch_options: dict[str, Any], preset: dict[str, Any] | None = None) -> dict[str, Any]:
    """Ask Camoufox to resolve a full fingerprint for these launch constraints.

    With a ``preset``, the fingerprint is built from a captured real device
    instead of being generated synthetically.

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
    if preset is not None:
        constraints["fingerprint_preset"] = preset

    try:
        resolved = camoufox_launch_options(**constraints)
        config = json.loads(resolved["env"]["CAMOU_CONFIG_1"])
    except Exception as exc:  # noqa: BLE001 - never block a launch over this
        logger.warning(f"Could not resolve a fingerprint to pin: {exc}")
        return {}

    frozen = freeze(config)
    source = "preset" if preset is not None else "generated"
    logger.info(f"Pinned a {source} fingerprint with {len(frozen)} properties")
    return frozen


# --- Real device presets -----------------------------------------------------
#
# Camoufox ships fingerprints captured from actual machines. A generated
# fingerprint is internally consistent but is still an assembly of parts; a
# preset is a combination that genuinely exists in the wild.


@lru_cache(maxsize=1)
def _presets() -> dict[str, list[dict[str, Any]]]:
    """Load the bundled presets, keyed by operating system.

    Camoufox picks its catalogue by Firefox version and falls back to a much
    smaller, older one when it is not told which. The version normally comes from
    the installed browser — but the presets themselves ship inside the Python
    package, so the catalogue must not disappear just because the browser has not
    been fetched yet. When the version is unavailable, ask for the modern
    catalogue directly.
    """
    try:
        from camoufox.fingerprints import PRESETS_V150_MIN_FF, load_presets
    except ImportError as exc:  # pragma: no cover - exercised only without camoufox
        logger.warning(f"Could not load fingerprint presets: {exc}")
        return {}

    try:
        from camoufox.pkgman import installed_verstr

        version: Any = installed_verstr()
    except Exception:  # noqa: BLE001 - the browser may not be installed yet
        version = PRESETS_V150_MIN_FF

    try:
        data = load_presets(version)
    except Exception as exc:  # noqa: BLE001 - presets are optional
        logger.warning(f"Could not load fingerprint presets: {exc}")
        return {}
    if not data:
        return {}
    presets = data.get("presets", data)
    return {os_name: entries for os_name, entries in presets.items() if isinstance(entries, list)}


def can_resolve() -> bool:
    """Whether a fingerprint can be resolved right now.

    Resolving asks Camoufox to build a full config, which needs the browser
    binary on disk — the presets themselves do not. So the catalogue can be
    listed before ``camoufox fetch`` has run, while pinning cannot.
    """
    try:
        from camoufox.pkgman import installed_verstr

        return bool(installed_verstr())
    except Exception:  # noqa: BLE001 - any failure means it is not usable
        return False


def describe_preset(preset: dict[str, Any]) -> dict[str, Any]:
    """Reduce a preset to the few values worth choosing between."""
    navigator = preset.get("navigator", {})
    screen = preset.get("screen", {})
    webgl = preset.get("webgl", {})
    width, height = screen.get("width"), screen.get("height")
    return {
        "screen": f"{width}x{height}" if width and height else None,
        "hardware_concurrency": navigator.get("hardwareConcurrency"),
        "gpu": webgl.get("unmaskedRenderer"),
        "vendor": webgl.get("unmaskedVendor"),
        "user_agent": navigator.get("userAgent"),
    }


def list_presets(os_name: str | None = None) -> list[dict[str, Any]]:
    """Return the available real device presets, newest catalogue first.

    Each entry carries an ``id`` of ``"<os>:<index>"``. The index is only a
    selector: once a profile is created the resolved fingerprint is pinned, so a
    later Camoufox update reshuffling the catalogue cannot move an existing
    profile onto different hardware.
    """
    catalogue = _presets()
    wanted = [os_name] if os_name else list(catalogue)
    entries = []
    for name in wanted:
        for index, preset in enumerate(catalogue.get(name, [])):
            entries.append({"id": f"{name}:{index}", "os": name, **describe_preset(preset)})
    return entries


def get_preset(preset_id: str) -> dict[str, Any] | None:
    """Look up one preset by the id from :func:`list_presets`."""
    os_name, _, index = preset_id.partition(":")
    entries = _presets().get(os_name, [])
    try:
        position = int(index)
    except ValueError:
        logger.warning(f"Unknown fingerprint preset {preset_id!r}")
        return None
    # Reject negatives explicitly: "windows:-1" would otherwise index from the
    # end and resolve to a real preset the id was never meant to name.
    if 0 <= position < len(entries):
        return entries[position]
    logger.warning(f"Unknown fingerprint preset {preset_id!r}")
    return None


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
