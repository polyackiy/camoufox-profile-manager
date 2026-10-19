"""Keep host graphics-driver dependencies paired with their host runtime.

PyInstaller's global library path otherwise makes Ubuntu 22.04's collected
libstdc++/libX11 shadow the newer libraries required by Ubuntu 24.04 Mesa.
Qt and the Python application remain bundled; these two libraries are native
system prerequisites for both the Debian package and portable distribution.
"""

from pathlib import PurePosixPath


def use_host_graphics_runtime(binaries):
    prefixes = ("libstdc++.so.", "libX11.so.")
    return [
        entry
        for entry in binaries
        if not PurePosixPath(entry[0]).name.startswith(prefixes)
    ]
