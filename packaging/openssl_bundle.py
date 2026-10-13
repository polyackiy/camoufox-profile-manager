"""Keep dynamically linked cryptography's OpenSSL pair together on macOS."""

from pathlib import Path


def select_openssl_pair(binaries, get_imports):
    """Resolve the pair actually used by Rust, replacing colliding Python copies.

    PyInstaller deduplicates libraries by destination name. On Intel macOS the
    source-built cryptography can require newer OpenSSL 3 symbols than Python's
    framework copy, even though both libraries have the same filename.
    """
    bindings = [
        source
        for destination, source, _ in binaries
        if destination.startswith("cryptography/") and Path(destination).name.startswith("_rust.")
    ]
    if not bindings:
        raise RuntimeError("Cryptography Rust bindings were not collected")
    names = {"libssl.3.dylib", "libcrypto.3.dylib"}
    linked = {
        Path(name).name: Path(source).resolve() if source else None
        for name, source in get_imports(bindings[0])
        if Path(name).name in names
    }
    if not linked:
        return binaries  # Official static wheels have no OpenSSL dylib dependency.
    if set(linked) != names or any(path is None or not path.is_file() for path in linked.values()):
        raise RuntimeError("Cannot resolve cryptography's complete dynamic OpenSSL pair")
    paths = list(linked.values())
    if paths[0].parent != paths[1].parent:
        raise RuntimeError("Cryptography OpenSSL libraries come from different installations")
    selected = []
    found = set()
    for destination, source, typecode in binaries:
        name = Path(destination).name
        if name in names:
            source = str(linked[name])
            found.add(name)
        selected.append((destination, source, typecode))
    if found != names:
        raise RuntimeError("The complete OpenSSL pair was not collected")
    print(
        "Bundling cryptography's OpenSSL pair:", {name: str(path) for name, path in linked.items()}
    )
    return selected
