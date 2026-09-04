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
import re
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


# Values that describe the browser *build* rather than the machine it runs on.
# A real computer updates its browser while staying the same computer, so these
# are the only parts a version refresh replaces. Everything else in the pin —
# GPU, screen, cores, fonts, noise seeds — is the device and must survive.
#
# Only navigator.userAgent carries the version in Camoufox 152 (the HTTP
# User-Agent header is derived from it, so freezing one keeps both in step, and
# navigator.buildID is not emitted). buildID is listed for when it is.
_BROWSER_VERSION_KEYS = frozenset(
    {
        "navigator.userAgent",
        "navigator.buildID",
    }
)

_UA_VERSION = re.compile(r"rv:(\d+)")


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


# Tokens that name the machine rather than the platform. Firefox leaves every
# one of them out of navigator.appVersion.
_APP_VERSION_DROPPED = ("Win64", "x64", "Mobile", "Tablet")
_USER_AGENT_PLATFORM = re.compile(r"Mozilla/5\.0 \(([^)]*)\)")


def _app_version_from_user_agent(user_agent: str) -> str | None:
    """The appVersion Firefox reports for a browser sending this user agent.

    "5.0 (<OS tokens>)": the parenthesised part of the user agent without the
    architecture or the Gecko revision, with Windows collapsed to its family
    name. Derived from the user agent rather than from navigator.platform
    because a fifth of the bundled Linux presets carry a distro token —
    "X11; Ubuntu" — that a platform lookup flattens to "X11", which no real
    Firefox reports. Checked against 800 captured fingerprints: exact on every
    one, Android and Ubuntu included.
    """
    block = _USER_AGENT_PLATFORM.match(user_agent or "")
    if not block:
        return None
    kept = []
    for token in (part.strip() for part in block.group(1).split(";")):
        if (
            token.startswith("rv:")
            or token in _APP_VERSION_DROPPED
            or token.startswith("Linux ")
            or token.startswith("Intel Mac OS X")
        ):
            continue
        kept.append("Windows" if token.startswith("Windows") else token)
    return f"5.0 ({'; '.join(kept)})" if kept else None


def _fill_app_version(pin: dict[str, Any]) -> None:
    """Give a pin the appVersion its own user agent implies, if it has none.

    Camoufox's generated fingerprints carry one; the ones built from a device
    preset do not, and an absent value falls through to the *host's* — a Linux
    preset pinned on a macOS machine reported "5.0 (Macintosh)" beside platform
    "Linux x86_64", which any page can read in two properties. Worse for a pin:
    the value would follow whichever machine opened the profile, so the same
    profile would not be the same computer. Reported and fixed upstream in
    daijro/camoufox#753; filled here so profiles pinned before that lands are
    whole.
    """
    if "navigator.appVersion" in pin:
        return
    user_agent = pin.get("navigator.userAgent")
    if not isinstance(user_agent, str):
        return
    derived = _app_version_from_user_agent(user_agent)
    if derived:
        pin["navigator.appVersion"] = derived


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
        if key not in ("user_data_dir", "persistent_context", "proxy")
    }
    constraints["headless"] = True
    # Resolve the machine offline. Only hardware is frozen — freeze() deliberately
    # leaves timezone, coordinates and WebRTC out, and those follow the proxy at
    # launch. Leaving geoip on made pinning reach the internet *through the proxy*
    # to geolocate it, so creating a profile from a device preset failed outright
    # whenever the proxy was unreachable, which has nothing to do with its screen
    # or its GPU.
    constraints["geoip"] = False
    if preset is not None:
        constraints["fingerprint_preset"] = preset

    try:
        resolved = camoufox_launch_options(**constraints)
        config = json.loads(resolved["env"]["CAMOU_CONFIG_1"])
    except Exception as exc:  # noqa: BLE001 - never block a launch over this
        logger.warning(f"Could not resolve a fingerprint to pin: {exc}")
        return {}

    frozen = freeze(config)
    _fill_app_version(frozen)
    source = "preset" if preset is not None else "generated"
    logger.info(f"Pinned a {source} fingerprint with {len(frozen)} properties")
    return frozen


# --- Real device presets -----------------------------------------------------
#
# Camoufox ships fingerprints captured from actual machines. A generated
# fingerprint is internally consistent but is still an assembly of parts; a
# preset is a combination that genuinely exists in the wild.


def _catalogue_version() -> Any:
    """The Firefox version Camoufox should pick its preset catalogue for.

    Camoufox falls back to a much smaller, older catalogue when it is not told
    which version to use. That version normally comes from the installed browser
    — but the presets themselves ship inside the Python package, so the catalogue
    must not disappear just because the browser has not been fetched yet.
    """
    from camoufox.fingerprints import PRESETS_V150_MIN_FF

    try:
        from camoufox.pkgman import installed_verstr

        return installed_verstr()
    except Exception:  # noqa: BLE001 - the browser may not be installed yet
        return PRESETS_V150_MIN_FF


@lru_cache(maxsize=4)
def _presets_for(version: Any) -> dict[str, list[dict[str, Any]]]:
    """Load the bundled presets for a Firefox version, keyed by operating system."""
    try:
        from camoufox.fingerprints import load_presets

        data = load_presets(version)
    except Exception as exc:  # noqa: BLE001 - presets are optional
        logger.warning(f"Could not load fingerprint presets: {exc}")
        return {}
    if not data:
        return {}
    presets = data.get("presets", data)
    return {os_name: entries for os_name, entries in presets.items() if isinstance(entries, list)}


def _presets() -> dict[str, list[dict[str, Any]]]:
    """Return the preset catalogue for the browser currently installed.

    Keyed on the version rather than cached outright, so a user who runs
    `camoufox fetch` while the app is up gets the right catalogue without a
    restart, and the parse is still done only once per version.
    """
    try:
        return _presets_for(_catalogue_version())
    except ImportError as exc:  # pragma: no cover - exercised only without camoufox
        logger.warning(f"Could not load fingerprint presets: {exc}")
        return {}


# navigator.platform is the pin's own statement of which OS it is, independent of
# whatever the profile's settings currently say.
_PLATFORM_OS = {
    "Win32": "windows",
    "Win64": "windows",
    "MacIntel": "macos",
    "Linux x86_64": "linux",
    "Linux i686": "linux",
}


def pinned_os(fingerprint: dict[str, Any] | None) -> str | None:
    """The operating system a pinned fingerprint describes.

    Read from the pin rather than the profile's settings, which can disagree with
    it: someone may change the OS dropdown long after the machine was pinned.
    Resolving a new user agent from the settings in that case would put a macOS
    browser on Windows hardware.
    """
    if not fingerprint:
        return None
    platform = str(fingerprint.get("navigator.platform", ""))
    if platform in _PLATFORM_OS:
        return _PLATFORM_OS[platform]
    oscpu = str(fingerprint.get("navigator.oscpu", "")).lower()
    for needle, name in (("windows", "windows"), ("mac", "macos"), ("linux", "linux")):
        if needle in oscpu:
            return name
    return None


def browser_major(fingerprint: dict[str, Any] | None) -> int | None:
    """The Firefox major version a pinned fingerprint claims, from its user agent."""
    if not fingerprint:
        return None
    match = _UA_VERSION.search(str(fingerprint.get("navigator.userAgent", "")))
    return int(match.group(1)) if match else None


def installed_major() -> int | None:
    """The Firefox major version of the browser on disk."""
    try:
        from camoufox.pkgman import installed_verstr

        match = re.match(r"(\d+)", installed_verstr())
        return int(match.group(1)) if match else None
    except Exception:  # noqa: BLE001 - the browser may not be installed
        return None


def is_outdated(fingerprint: dict[str, Any] | None, installed: int | None = None) -> bool:
    """Whether a pin claims an older browser than the one now installed.

    A pinned fingerprint never ages on its own, so a profile kept for months
    keeps advertising the version it was created with. Real machines update, and
    a browser several releases behind is itself unusual enough to notice.

    ``installed`` lets a caller that already knows the version pass it in.
    Reading it costs a config file, and a profile list would otherwise pay for
    that once per profile per field.
    """
    pinned = browser_major(fingerprint)
    current = installed_major() if installed is None else installed
    return pinned is not None and current is not None and pinned < current


def refresh_browser_version(pinned: dict[str, Any], resolved: dict[str, Any]) -> dict[str, Any]:
    """Move a pin onto a newer browser without changing the machine.

    Takes the browser-version values from ``resolved`` and keeps everything else
    from ``pinned``, which is what a real device looks like after an update: same
    GPU, screen, cores, fonts and noise seeds; newer user agent.
    """
    updated = dict(pinned)
    for key in _BROWSER_VERSION_KEYS:
        if key in resolved:
            updated[key] = resolved[key]
        else:
            # The new build no longer emits it; a stale value would be worse.
            updated.pop(key, None)
    return updated


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

    # Only a plain run of ASCII digits names a preset. int() is far more
    # permissive: it accepts a sign, surrounding whitespace, underscores as digit
    # separators and non-ASCII digits, so "windows:-1" indexed from the end and
    # "windows:1_0" resolved to index 10 — a different device than the id reads as.
    if not (index.isascii() and index.isdigit()):
        logger.warning(f"Unknown fingerprint preset {preset_id!r}")
        return None

    position = int(index)
    if position < len(entries):
        return entries[position]
    logger.warning(f"Unknown fingerprint preset {preset_id!r}")
    return None


def summarize(
    fingerprint: dict[str, Any] | None, settings_os: str | None = None
) -> dict[str, Any] | None:
    """Describe a pinned fingerprint in the few values worth showing a user.

    The stored config is ~40 properties and tens of kilobytes; this is what the
    UI displays so someone can confirm the profile really has a fixed machine.

    ``settings_os`` is the profile's own OS setting, reported next to the OS the
    pin describes so the two can be seen to disagree. While a pin exists the
    setting has no effect on what a page sees, so a disagreement is silent
    otherwise.
    """
    if not fingerprint:
        return None

    width = fingerprint.get("screen.width")
    height = fingerprint.get("screen.height")
    fonts = fingerprint.get("fonts")
    installed = installed_major()
    pinned = pinned_os(fingerprint)
    return {
        "user_agent": fingerprint.get("navigator.userAgent"),
        "platform": fingerprint.get("navigator.platform"),
        "hardware_concurrency": fingerprint.get("navigator.hardwareConcurrency"),
        "screen": f"{width}x{height}" if width and height else None,
        "gpu": fingerprint.get("webGl:renderer"),
        "font_count": len(fonts) if isinstance(fonts, list) else None,
        "property_count": len(fingerprint),
        # So the UI can offer an update without re-deriving this itself.
        "browser_major": browser_major(fingerprint),
        "installed_major": installed,
        "browser_outdated": is_outdated(fingerprint, installed),
        "pinned_os": pinned,
        "settings_os": settings_os,
        "os_mismatch": bool(pinned and settings_os and pinned != settings_os),
    }
