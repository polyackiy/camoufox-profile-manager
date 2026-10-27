"""Normalize known Ubuntu CI mirrors without changing APT's trust policy.

Run only against a disposable runner or a fixture root. Mirror-list priorities,
source suites/components/options and Signed-By fields are preserved verbatim.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from urllib.parse import urlsplit

NETWORK_CONFIG = (
    'Acquire::Retries "2";\nAcquire::http::Timeout "30";\nAcquire::https::Timeout "30";\n'
)
HOSTS = {
    "azure.archive.ubuntu.com": "archive.ubuntu.com",
    "archive.ubuntu.com": "archive.ubuntu.com",
    "security.ubuntu.com": "security.ubuntu.com",
}


def normalize_uri(value: str) -> str:
    if any(character.isspace() or ord(character) < 32 for character in value):
        return value
    try:
        uri = urlsplit(value)
        host = uri.hostname
    except ValueError:
        return value
    if (
        uri.scheme not in {"http", "https"}
        or host not in HOSTS
        or uri.netloc.lower() != host
        or uri.path not in {"/ubuntu", "/ubuntu/"}
        or uri.query
        or uri.fragment
        or "?" in value
        or "#" in value
    ):
        return value
    return f"https://{HOSTS[host]}{uri.path}"


def normalize_sources(text: str, kind: str) -> str:
    lines = []
    field = None
    for line in text.splitlines(keepends=True):
        if not line.strip():
            field = None
        elif not line.lstrip().startswith("#"):
            if kind == ".sources":
                header = re.match(r"^([A-Za-z][A-Za-z0-9-]*):", line)
                if header:
                    field = header[1].lower()
                    if field == "uris":
                        prefix, values = line.split(":", 1)
                        line = prefix + ":" + re.sub(r"\S+", lambda m: normalize_uri(m[0]), values)
                elif line[:1].isspace() and field == "uris":
                    line = re.sub(r"\S+", lambda m: normalize_uri(m[0]), line)
            elif kind == ".list":
                line = re.sub(
                    r"^([ \t]*(?:deb|deb-src)[ \t]+(?:\[[^\]\r\n]*\][ \t]+)?)(\S+)",
                    lambda m: m[1] + normalize_uri(m[2]),
                    line,
                )
            elif kind == "mirror":
                line = re.sub(r"^([ \t]*)(\S+)", lambda m: m[1] + normalize_uri(m[2]), line)
            else:
                raise ValueError("Unsupported APT source format")
        lines.append(line)
    return "".join(lines)


def regular_path(root: Path, path: Path) -> None:
    """A privileged provisioning step must not follow links out of its root."""
    if root.is_symlink() or not path.is_relative_to(root):
        raise ValueError("Refusing linked APT configuration")
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Refusing linked APT configuration")
    if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
        raise ValueError("APT configuration must be an unshared regular file")


def configure(root: Path) -> list[str]:
    root = root.absolute()
    apt = root / "etc/apt"
    if root.is_symlink() or not apt.is_dir():
        raise ValueError("An existing APT root is required")
    sources = apt / "sources.list.d"
    if sources.is_symlink() or (sources.exists() and not sources.is_dir()):
        raise ValueError("Refusing linked APT configuration")
    paths = [(apt / "sources.list", ".list"), (apt / "apt-mirrors.txt", "mirror")]
    if sources.is_dir():
        paths += [
            (p, p.suffix) for p in sorted(sources.iterdir()) if p.suffix in {".list", ".sources"}
        ]
    plans = []
    for path, kind in paths:
        regular_path(root, path)
        if path.exists():
            original = path.read_bytes()
            updated = normalize_sources(original.decode("utf-8"), kind).encode("utf-8")
            if updated != original:
                plans.append((path, updated))
    config = apt / "apt.conf.d/99-cpm-ci-network"
    regular_path(root, config)
    expected = NETWORK_CONFIG.encode("ascii")
    if not config.exists() or config.read_bytes() != expected:
        plans.append((config, expected))
    # Validate every destination before changing any source. Only our dedicated
    # network timeout file is created; signing/keyring configuration is untouched.
    for path, _ in plans:
        regular_path(root, path)
    for path, data in plans:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return [path.relative_to(root).as_posix() for path, _ in plans]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Disposable Ubuntu APT root")
    args = parser.parse_args()
    try:
        changed = configure(args.root)
    except (OSError, ValueError, UnicodeError):
        parser.exit(1, "APT normalization failed: check regular source files and permissions.\n")
    print(f"APT CI: normalized {len(changed)} files; retries=2, HTTP(S) timeout=30 seconds")


if __name__ == "__main__":
    main()
