"""Command-line launcher: run the API + bundled web UI as one process.

Installed as the ``camoufox-pm`` console script. Starts the server and opens the
web UI in the default browser.
"""

import argparse
import os
import threading
import webbrowser

import uvicorn

from camoufox_pm.config import get_settings


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        prog="camoufox-pm",
        description="Run the Camoufox Profile Manager (API + web UI) on one port.",
    )
    parser.add_argument("--host", default=settings.host, help="Bind address")
    parser.add_argument("--port", type=int, default=settings.port, help="Port")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser")
    parser.add_argument(
        "--desktop",
        action="store_true",
        help="Open a native desktop window instead of a browser tab (needs the 'desktop' extra)",
    )
    args = parser.parse_args()

    # Make the settings match what we are about to bind, so everything that reads
    # them (the Settings screen, CORS, logs) reports the real address rather than
    # the default the flags just overrode.
    os.environ["CPM_HOST"] = args.host
    os.environ["CPM_PORT"] = str(args.port)
    get_settings.cache_clear()

    if args.desktop:
        from camoufox_pm.desktop import run_desktop

        run_desktop(host=args.host, port=args.port)
        return

    if not args.no_browser:
        url = f"http://{'localhost' if args.host in ('0.0.0.0', '127.0.0.1') else args.host}:{args.port}/"
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    # Import the app object (not an import string) so this works inside a frozen
    # PyInstaller bundle where uvicorn cannot resolve the module by name.
    from camoufox_pm.main import app

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
