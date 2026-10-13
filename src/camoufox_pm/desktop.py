"""Native desktop launcher with persistent storage and a private local server."""

import contextlib
import html
import os
import socket
import stat
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


def _stop_server(server: uvicorn.Server, thread: threading.Thread) -> bool:
    """Finish lifespan cleanup before permitting a native application quit."""
    server.should_exit = True
    thread.join(timeout=15)
    if thread.is_alive():
        server.force_exit = True
        thread.join(timeout=3)
    # Uvicorn's force-exit path can skip lifespan cleanup. A stopped thread
    # alone is not enough to authorize the successful packaged-process exit.
    return not thread.is_alive() and not server.force_exit


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
                window = webview.create_window(
                    title, f"http://{ui_host}:{actual_port}/", width=1280, height=800
                )
                if window is None:
                    raise RuntimeError("The native window could not be created.")

                def monitor_updates():
                    from camoufox_pm.core.update_runtime import _record

                    while thread.is_alive():
                        if (_record() or {}).get("action"):
                            window.destroy()
                            return
                        time.sleep(0.5)

                threading.Thread(target=monitor_updates, daemon=True).start()
                if sys.platform == "darwin":
                    # Cocoa Cmd+Q may terminate NSApplication without returning
                    # from webview.start(). Its synchronous closing event must
                    # finish API/browser cleanup first (False cancels the quit).
                    window.events.closing += lambda: _stop_server(server, thread)
                if sys.platform.startswith("linux"):
                    icon_root = Path(
                        getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2] / "packaging")
                    )
                    webview.start(icon=str(icon_root / "camoufox-pm.svg"))
                else:
                    webview.start()
            finally:
                # Lifespan closes running browser sessions and the database.
                if not _stop_server(server, thread):
                    raise RuntimeError("The local server did not finish shutting down.")
                from camoufox_pm.core.update_runtime import _record, spawn_helper

                if (_record() or {}).get("action"):
                    spawn_helper()


def finish_packaged_desktop() -> None:
    """End a cleaned-up macOS bundle without re-entering native module finalizers.

    The frozen Cocoa process can hang in CPython's final module GC after the
    window, browser driver and API have all stopped (#67). Only the standalone
    macOS entry point uses this boundary, after main() returns successfully.
    Database/browser cleanup and the instance-lock release happen in run_desktop,
    not in atexit handlers. Closing the process also closes the tracker's pipe.
    """
    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        return
    import logging

    logging.shutdown()
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:
            with contextlib.suppress(OSError, ValueError):
                stream.flush()
    os._exit(0)


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


def _run_packaged_recovery() -> None:
    """Recovery is a console command: errors must never wait for a GUI dialog."""
    output = None
    previous_stdout, previous_stderr = sys.stdout, sys.stderr
    try:
        from camoufox_pm.config import Settings, desktop_data_dir, get_settings

        configured = Settings()
        if "db_path" not in configured.model_fields_set:
            os.environ["CPM_DB_PATH"] = str(desktop_data_dir() / "profiles.db")
        get_settings.cache_clear()
        if "--desktop" not in sys.argv[1:] and not any(
            flag in sys.argv[1:] for flag in ("--update-helper", "--update-validate")
        ):
            sys.argv.insert(1, "--desktop")
        if sys.stdout is None or sys.stderr is None:
            control = Path(get_settings().db_path).expanduser().resolve().parent / "update-control"
            output_path = control / "recovery-command.log"
            if os.name != "nt":
                for path in (control, *control.parents):
                    if path.is_symlink() or (path.exists() and not path.is_dir()):
                        raise RuntimeError(
                            "Recovery log directory contains a symlink or non-directory"
                        )
                if control.parent.exists() and (
                    control.parent.stat().st_uid != os.geteuid()
                    or stat.S_IMODE(control.parent.stat().st_mode) & 0o022
                ):
                    raise RuntimeError(
                        "Recovery log parent must be owned and writable only by this user"
                    )
                if control.exists() and control.stat().st_uid != os.geteuid():
                    raise RuntimeError("Recovery log directory is owned by another user")
                if output_path.is_symlink() or (
                    output_path.exists()
                    and (not output_path.is_file() or output_path.stat().st_uid != os.geteuid())
                ):
                    raise RuntimeError("Recovery log must be a regular file owned by this user")
            control.mkdir(parents=True, exist_ok=True, mode=0o700)
            if os.name == "nt":
                from camoufox_pm.core.update_permissions import create_private_file, protect_path

                protect_path(control)
                if not output_path.exists():
                    try:
                        os.close(create_private_file(output_path))
                    except FileExistsError:
                        pass
                protect_path(output_path)
                output = output_path.open("a", encoding="utf-8", buffering=1)
            else:
                # Establish the log using only stdlib before importing core's
                # crypto/native dependencies, so loader failures remain visible.
                control.chmod(0o700)
                flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(output_path, flags, 0o600)
                os.fchmod(descriptor, 0o600)
                output = os.fdopen(descriptor, "a", encoding="utf-8", buffering=1)
            sys.stdout = sys.stderr = output
        from camoufox_pm import cli

        cli.main()
    except Exception as exc:
        import traceback

        if sys.stderr is not None:
            traceback.print_exc(file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        if output is not None:
            sys.stdout, sys.stderr = previous_stdout, previous_stderr
            output.close()


def main() -> None:
    """Double-click entry point; ordinary ``camoufox-pm`` keeps its CLI behavior."""
    if "update-recovery" in sys.argv[1:] or any(
        flag in sys.argv[1:] for flag in ("--update-helper", "--update-validate")
    ):
        _run_packaged_recovery()
        return
    try:
        # Locate recovery state before bootstrap can create an encryption key.
        from camoufox_pm.config import Settings, bootstrap_desktop, desktop_data_dir, get_settings

        configured = Settings()
        if "db_path" not in configured.model_fields_set:
            os.environ["CPM_DB_PATH"] = str(desktop_data_dir() / "profiles.db")
        get_settings.cache_clear()
        from camoufox_pm.core.maintenance import configured_gate
        from camoufox_pm.core.update_runtime import _record, request, spawn_helper, status

        if (
            configured_gate().blocked
            or (_record() or {}).get("action")
            or "--update-recovery-window" in sys.argv
        ):
            from camoufox_pm.desktop_recovery import show_recovery

            def dispatch(action, transaction_id, timestamp):
                if action == "cancel":
                    from camoufox_pm.core.update_runtime import cancel

                    return cancel()
                result = request(action, transaction_id, timestamp)
                spawn_helper()
                return result

            if not show_recovery(status, dispatch):
                return
            if "--update-recovery-window" in sys.argv:
                from camoufox_pm.core.update_runtime import reopen_desktop

                reopen_desktop()
                return
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
