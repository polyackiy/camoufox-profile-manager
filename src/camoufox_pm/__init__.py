"""Camoufox Profile Manager — antidetect browser profile management on Camoufox."""

import sys
from importlib.metadata import PackageNotFoundError, version

# Read from the installed package rather than repeating the number here. The two
# had already drifted: a wheel built as 0.2.0 reported 0.1.1 through /health and
# in the OpenAPI schema, because releasing means editing pyproject.toml and it is
# the second copy that gets forgotten.
_build_version = (
    getattr(sys, "_camoufox_pm_build_version", None) if getattr(sys, "frozen", False) else None
)
if _build_version:
    # Desktop runtime hook is compiled into this executable. Old dist-info may
    # remain alongside it after an upgrade and must not determine its version.
    __version__ = _build_version
else:
    try:
        __version__ = version("camoufox-profile-manager")
    except PackageNotFoundError:  # pragma: no cover - only when running from a source tree
        __version__ = "0.0.0+unknown"

__description__ = "Self-hosted, open-source antidetect browser profile manager built on Camoufox"
