"""Bind a desktop executable to its build version rather than adjacent metadata."""

from pathlib import Path


def write_runtime_version_hook(path: Path, version: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "import sys\n" + f"sys._camoufox_pm_build_version = {version!r}\n",
        encoding="utf-8",
    )
