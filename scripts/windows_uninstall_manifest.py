"""Compile an immutable, exact-file NSIS removal list from the Windows bundle.

Never consume a writable on-disk uninstall manifest. Unknown files and nonempty
folders survive removal. Reject names that NSIS/Win32 could interpret as syntax,
wildcards, aliases or alternate data streams rather than literal bundle paths.
"""

import os
import stat
from pathlib import Path

_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(10)),
    *(f"LPT{i}" for i in range(10)),
}


def literal_path(path: Path) -> str:
    for part in path.parts:
        if (
            not part
            or part in {".", ".."}
            or part.endswith((" ", "."))
            or any(ord(c) < 32 or c in '<>:"/\\|?*$' for c in part)
            or part.split(".")[0].upper() in _RESERVED
        ):
            raise ValueError("Unsafe Windows bundle filename")
    return "\\".join(path.parts)


def write_uninstall_manifest(bundle: Path, output: Path) -> None:
    files, directories, seen = [], [], set()
    for root, dirs, names in os.walk(bundle, followlinks=False):
        for name in sorted([*dirs, *names]):
            path = Path(root) / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("Windows bundle must not contain reparse points")
            relative = literal_path(path.relative_to(bundle))
            if relative.casefold() in seen or relative.casefold() == "uninstall.exe":
                raise ValueError("Ambiguous or reserved Windows bundle filename")
            seen.add(relative.casefold())
            if stat.S_ISDIR(info.st_mode):
                directories.append(relative)
            elif stat.S_ISREG(info.st_mode):
                files.append(relative)
            else:
                raise ValueError("Windows bundle contains a special file")
    if "camoufox-pm.exe" not in files:
        raise ValueError("Windows bundle has no manager executable")
    lines = [
        "; Generated from the bundle at build time. Do not edit.",
        "!macro ValidateBundlePaths PREFIX",
        '  Push "$INSTDIR"',
        "  Call ${PREFIX}ValidateAncestors",
    ]
    for path in sorted([*directories, *files, "Uninstall.exe"]):
        lines += [f'  Push "$INSTDIR\\{path}"', "  Call ${PREFIX}ValidatePath"]
    lines += ["!macroend", "!macro RemoveBundleFiles"]
    for path in sorted(files):
        lines.append(f'  !insertmacro RemoveOwnedFile "$INSTDIR\\{path}"')
    for path in sorted(directories, key=lambda p: (-p.count("\\"), p)):
        lines.append(f'  RMDir "$INSTDIR\\{path}"')
    lines += ["!macroend", ""]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
