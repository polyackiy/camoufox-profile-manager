"""Native desktop launcher with persistent storage and a private local server."""

import contextlib
import html
import os
import socket
import sys
import threading
import time
from pathlib import Path

import uvicorn


class AlreadyRunningError(RuntimeError):
    """The same desktop data directory is already open in another process."""


@contextlib.contextmanager
def _instance_lock(data_dir: Path):
    """Hold an OS lock for the process lifetime; crashes release it automatically."""
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / "desktop.lock").open("a+b") as lock:
        lock.seek(0)
        if lock.read(1) == b"":
            lock.write(b"\0")
            lock.flush()
        lock.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise AlreadyRunningError(
                "Camoufox Profile Manager is already open. Switch to its existing window."
            ) from exc
        try:
            yield
        finally:
            lock.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _wait_until_serving(
    server: uvicorn.Server, thread: threading.Thread, timeout: float = 30
) -> bool:
    """Wait for this server's lifespan startup, never for an unrelated open port."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and thread.is_alive():
        if server.started:
            return True
        time.sleep(0.05)
    return False


def run_desktop(
    host: str = "127.0.0.1", port: int = 0, title: str = "Camoufox Profile Manager"
) -> None:
    """Serve on a reserved socket and stop browsers/server when the window closes."""
    try:
        import webview
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "Desktop mode needs pywebview. From source, run: uv sync --extra desktop"
        ) from exc

    from camoufox_pm.config import bootstrap_desktop, get_settings

    bootstrap_desktop()
    settings = get_settings()
    with _instance_lock(Path(settings.db_path).resolve().parent):
        # Bind before starting uvicorn: port=0 chooses an available port, and an
        # explicit occupied port fails before any webview can open the wrong app.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            if sys.platform == "win32":
                # Windows SO_REUSEADDR can permit another process to steal a port.
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                listener.bind((host, port))
            except OSError as exc:
                raise RuntimeError(
                    f"Cannot start on {host}:{port}. Close the app using that port, "
                    "or start Camoufox Profile Manager without a fixed port."
                ) from exc
            listener.listen(128)
            actual_port = listener.getsockname()[1]
            os.environ["CPM_HOST"] = host
            os.environ["CPM_PORT"] = str(actual_port)
            get_settings.cache_clear()
            from camoufox_pm.main import app

            server = uvicorn.Server(
                uvicorn.Config(app, host=host, port=actual_port, log_level="info")
            )
            thread = threading.Thread(
                target=server.run, kwargs={"sockets": [listener]}, daemon=True
            )
            thread.start()
            try:
                if not _wait_until_serving(server, thread):
                    raise RuntimeError(
                        "The local server could not start. See desktop.log in your data folder."
                    )
                ui_host = "127.0.0.1" if host in ("0.0.0.0", "127.0.0.1") else host
                webview.create_window(
                    title, f"http://{ui_host}:{actual_port}/", width=1280, height=800
                )
                webview.start()
            finally:
                server.should_exit = True
                # Lifespan closes running browser sessions and the database.
                thread.join(timeout=15)
                if thread.is_alive():
                    server.force_exit = True
                    thread.join(timeout=3)


def _show_error(message: str) -> None:
    """Windowed builds need a visible error even though no console is attached."""
    try:
        import webview

        webview.create_window(
            "Camoufox Profile Manager",
            html=(
                "<html><body style='font:18px system-ui;padding:24px'>"
                "<h1>Camoufox Profile Manager</h1><p>" + html.escape(message) + "</p></body></html>"
            ),
            width=600,
            height=300,
        )
        webview.start()
    except Exception:
        if sys.stderr is not None:
            print(message, file=sys.stderr)


def main() -> None:
    """Double-click entry point; ordinary ``camoufox-pm`` keeps its CLI behavior."""
    try:
        from camoufox_pm.config import bootstrap_desktop, get_settings

        bootstrap_desktop()
        # PyInstaller windowed apps have no stdout/stderr. Keep logs writable
        # outside the immutable .app / installation directory.
        log_path = Path(get_settings().db_path).resolve().parent / "desktop.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if getattr(sys, "frozen", False) or sys.stdout is None or sys.stderr is None:
            log = log_path.open("a", encoding="utf-8", buffering=1)
            sys.stdout = log
            sys.stderr = log
        run_desktop()
    except Exception as exc:
        import traceback

        if sys.stderr is not None:
            traceback.print_exc(file=sys.stderr)
        _show_error(str(exc))
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
