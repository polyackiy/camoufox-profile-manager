"""Desktop mode: run the server in the background and show a native window.

Requires the optional ``desktop`` extra (pywebview). Used by ``camoufox-pm
--desktop`` and by the packaged desktop app.
"""

import contextlib
import socket
import threading
import time

import uvicorn


def _wait_until_serving(host: str, port: int, timeout: float = 30.0) -> bool:
    """Block until the server accepts connections, or the timeout elapses."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with contextlib.suppress(OSError), socket.create_connection((host, port), timeout=1):
            return True
        time.sleep(0.2)
    return False


def run_desktop(
    host: str = "127.0.0.1", port: int = 8000, title: str = "Camoufox Profile Manager"
) -> None:
    """Start the API server in a thread and open a native desktop window."""
    try:
        import webview
    except ImportError as exc:  # pragma: no cover - depends on the optional extra
        raise SystemExit(
            "Desktop mode needs the 'desktop' extra. Install it with:\n"
            "  uv sync --extra desktop      # or\n"
            "  pip install 'camoufox-profile-manager[desktop]'"
        ) from exc

    # Import the app object (not an import string) so this works inside a frozen
    # PyInstaller bundle where uvicorn cannot resolve the module by name.
    from camoufox_pm.main import app

    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    if not _wait_until_serving(host, port):
        server.should_exit = True
        raise SystemExit(f"Server did not start on {host}:{port}")

    ui_host = "localhost" if host in ("0.0.0.0", "127.0.0.1") else host
    webview.create_window(title, f"http://{ui_host}:{port}/", width=1280, height=800)
    webview.start()

    # The window was closed — stop the server and exit.
    server.should_exit = True
