"""Command-line launcher: run the API + bundled web UI as one process.

Installed as the ``camoufox-pm`` console script. Starts the server and opens the
web UI in the default browser.
"""

import argparse
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
    args = parser.parse_args()

    if not args.no_browser:
        url = f"http://{'localhost' if args.host in ('0.0.0.0', '127.0.0.1') else args.host}:{args.port}/"
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    uvicorn.run("camoufox_pm.main:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
