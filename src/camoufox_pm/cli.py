"""Command-line launcher: run the API + bundled web UI as one process.

Installed as the ``camoufox-pm`` console script. Starts the server and opens the
web UI in the default browser. The ``user`` subcommands manage login accounts —
they exist on the CLI, not the API, so the first user can be created without a
chicken-and-egg lockout and creating accounts requires shell access to the host.
"""

import argparse
import asyncio
import getpass
import os
import sys
import threading
import webbrowser
from typing import NoReturn

import uvicorn

from camoufox_pm.config import get_settings
from camoufox_pm.core.database import StorageManager
from camoufox_pm.core.leases import lease_expired
from camoufox_pm.core.maintenance import configured_gate


def _admitted_command(function):
    async def admitted(*args, **kwargs):
        with configured_gate().operation():
            return await function(*args, **kwargs)

    return admitted


def main() -> None:
    if sys.argv[1:] == ["--sync-recovery-verify"]:
        from camoufox_pm.sync.recovery import recovery_worker_main

        raise SystemExit(recovery_worker_main())
    # These fixed internal commands run before desktop bootstrap and DB startup.
    if len(sys.argv) >= 2 and sys.argv[1] == "--update-validate":
        if len(sys.argv) != 3:
            raise SystemExit("Validation requires exactly one helper receipt")
        from camoufox_pm.core.update_validation import validate_installed_process

        validate_installed_process(sys.argv[2])
        return
    if len(sys.argv) >= 2 and sys.argv[1] == "--update-helper":
        helper = argparse.ArgumentParser(prog="camoufox-pm --update-helper")
        helper.add_argument("action", choices=("apply", "resume", "rollback"))
        helper.add_argument("--confirm-timestamp")
        helper.add_argument("--wait-pid", type=int)
        helper.add_argument("--wait-created", type=float)
        args = helper.parse_args(sys.argv[2:])
        if args.wait_pid is not None:
            if args.wait_created is None or args.wait_pid <= 0:
                helper.error("wait requires a process identity")
            import psutil

            try:
                parent = psutil.Process(args.wait_pid)
                if abs(parent.create_time() - args.wait_created) < 0.01:
                    parent.wait(timeout=60)
            except psutil.NoSuchProcess:
                pass
            except (psutil.TimeoutExpired, psutil.AccessDenied) as exc:
                raise SystemExit("The manager did not exit; offline update refused") from exc
        from camoufox_pm.config import Settings, desktop_data_dir
        from camoufox_pm.core.update_runtime import execute_request, reopen_desktop

        configured = Settings()
        if "db_path" not in configured.model_fields_set:
            os.environ["CPM_DB_PATH"] = str(desktop_data_dir() / "profiles.db")
        get_settings.cache_clear()
        if args.action == "rollback" and not args.confirm_timestamp:
            helper.error("rollback requires --confirm-timestamp")
        try:
            if args.action == "rollback":
                execute_request("rollback", confirmed_timestamp=args.confirm_timestamp)
            else:
                execute_request(args.action)
        except Exception:
            reopen_desktop()
            raise
        reopen_desktop()
        return
    if "--desktop" in sys.argv[1:]:
        from camoufox_pm.config import bootstrap_desktop

        if "update-recovery" in sys.argv[1:]:
            # Recovery must not generate a key if a restore temporarily renamed
            # both the database and original key. Resolve desktop defaults only.
            from camoufox_pm.config import Settings, desktop_data_dir

            configured = Settings()
            if "db_path" not in configured.model_fields_set:
                os.environ["CPM_DB_PATH"] = str(desktop_data_dir() / "profiles.db")
            get_settings.cache_clear()
        else:
            bootstrap_desktop()
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

    subcommands = parser.add_subparsers(dest="command")
    user_parser = subcommands.add_parser(
        "user",
        help="Manage login accounts (creating the first one turns login on)",
        description=(
            "Manage web UI login accounts. As long as any account exists, the API "
            "requires a login session or the API key; removing the last one turns "
            "login off again."
        ),
    )
    user_commands = user_parser.add_subparsers(dest="user_command", required=True)

    add = user_commands.add_parser("add", help="Create an account (prompts for the password)")
    add.add_argument("username")
    add.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read the password from the first line of stdin instead of prompting",
    )

    passwd = user_commands.add_parser("passwd", help="Change an account's password")
    passwd.add_argument("username")
    passwd.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read the password from the first line of stdin instead of prompting",
    )

    remove = user_commands.add_parser("remove", help="Delete an account and its sessions")
    remove.add_argument("username")

    user_commands.add_parser("list", help="List accounts (never shows password hashes)")

    # Shell-only on purpose, like the user commands above. A force-unlock in the
    # HTTP API would become a button, and a button is the shortest path back to
    # two machines driving one identity — the corruption the lease prevents.
    unlock = subcommands.add_parser(
        "unlock",
        help="Force-release the lease on a profile",
        description=(
            "Clear the lease holding a profile, whatever it says. Needed only when a "
            "machine died in a way its lease cannot notice, or when you have checked "
            "that the holder is really gone: a live lease means another instance may "
            "be driving this profile right now."
        ),
    )
    unlock.add_argument("profile_id")
    unlock.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")

    subcommands.add_parser(
        "leases",
        help="List the profiles currently leased, and by whom",
    )

    recovery = subcommands.add_parser(
        "update-recovery",
        help="Inspect or cancel interrupted update preparation without opening application data",
    )
    recovery.add_argument("action", choices=("status", "cancel", "checkpoint", "restore"))

    recovery.add_argument("--checkpoint-id")
    recovery.add_argument(
        "--confirm-timestamp",
        help="Exact UTC checkpoint timestamp; acknowledges loss of later changes",
    )

    args = parser.parse_args()

    if args.command == "update-recovery":
        import json

        from camoufox_pm.core.update_service import UpdateService

        gate = configured_gate()
        service = UpdateService(gate.path.parent, gate)
        if args.action == "checkpoint":
            from camoufox_pm.core.update_recovery import offline_checkpoint

            checkpoint_id = offline_checkpoint()
            checkpoint_journal = service.root / "checkpoints" / checkpoint_id / "journal.json"
            timestamp = json.loads(checkpoint_journal.read_text())["created_at"]
            metadata = json.loads((checkpoint_journal.parent / "key-reference.json").read_text())
            versions = {
                key: metadata.get(key)
                for key in ("manager_version", "wrapper_version", "browser_active")
            }
            print(
                json.dumps(
                    {"checkpoint_id": checkpoint_id, "created_at": timestamp, **versions}, indent=2
                )
            )
            return
        if args.action == "restore":
            from camoufox_pm.core.update_recovery import offline_restore

            if not args.checkpoint_id or not args.confirm_timestamp:
                parser.error("restore requires --checkpoint-id and --confirm-timestamp")
            offline_restore(args.checkpoint_id, args.confirm_timestamp)
            print("Complete data and browser checkpoint restored; newer copies were retained.")
            return
        if args.action == "cancel":
            if service.status()["state"] == "checkpoint_interrupted":
                service.cancel_abandoned_checkpoint()
            else:
                service.cancel_preparation()
        status = service.status()
        status["checkpoints"] = []
        for journal_path in sorted((service.root / "checkpoints").glob("*/journal.json")):
            try:
                journal = json.loads(journal_path.read_text())
                metadata = json.loads((journal_path.parent / "key-reference.json").read_text())
                versions = {
                    key: metadata.get(key)
                    for key in ("manager_version", "wrapper_version", "browser_active")
                }
                status["checkpoints"].append(
                    {
                        "checkpoint_id": journal_path.parent.name,
                        "created_at": journal.get("created_at"),
                        "state": journal.get("state"),
                        **versions,
                    }
                )
            except (OSError, ValueError):
                continue
        print(json.dumps(status, indent=2))
        return

    if args.command == "user":
        asyncio.run(_run_user_command(args))
        return

    if args.command == "unlock":
        asyncio.run(_run_unlock_command(args))
        return

    if args.command == "leases":
        asyncio.run(_run_leases_command())
        return

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


def _fail(message: str) -> NoReturn:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def _read_password(args: argparse.Namespace) -> str:
    """Collect a password without it ever appearing in argv or the environment."""
    from camoufox_pm.core.auth import MIN_PASSWORD_LENGTH

    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass.getpass("Password: ")
        if getpass.getpass("Repeat password: ") != password:
            _fail("Passwords do not match.")
    if len(password) < MIN_PASSWORD_LENGTH:
        _fail(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    return password


@_admitted_command
async def _run_unlock_command(args: argparse.Namespace) -> None:
    """Force-release one profile's lease; shell-only (see the parser)."""
    storage = StorageManager(get_settings().db_path)
    await storage.initialize()
    try:
        lease = await storage.get_lease(args.profile_id)
        if lease is None:
            _fail(f"No profile with ID '{args.profile_id}'.")
        holder, expires = lease
        if holder is None:
            print(f"Profile {args.profile_id} is not leased.")
            return
        state = "expired" if lease_expired(expires) else "live"
        print(
            f"Profile {args.profile_id} is leased by {holder}"
            + (f" until {expires} UTC ({state})" if expires else " (no expiry)")
        )
        if not args.yes and input("Force-release this lease? (yes/no): ").strip().lower() not in (
            "yes",
            "y",
        ):
            print("Cancelled; the lease stands.")
            return
        previous = await storage.force_release_lease(args.profile_id)
        print(f"Lease released (was held by {previous}).")
    finally:
        await storage.close()


@_admitted_command
async def _run_leases_command() -> None:
    """Show who holds what, so `unlock` is a decision and not a guess."""
    storage = StorageManager(get_settings().db_path)
    await storage.initialize()
    try:
        holders = await storage.get_lease_holders()
        if not holders:
            print("No profiles are leased.")
            return
        for entry in holders:
            state = "expired" if entry["expired"] else "live"
            print(
                f"{entry['id']}  {entry['name']}  {entry['locked_by']}  "
                f"expires {entry['lock_expires']} UTC ({state})"
            )
    finally:
        await storage.close()


@_admitted_command
async def _run_user_command(args: argparse.Namespace) -> None:
    # Imported here so plain `camoufox-pm` startup does not pay for them.
    from camoufox_pm.api.principal import Principal
    from camoufox_pm.core import auth
    from camoufox_pm.core.database import StorageManager

    storage = StorageManager(get_settings().db_path)
    await storage.initialize()
    try:
        if args.user_command == "add":
            password = _read_password(args)
            try:
                await storage.create_user(
                    auth.new_user_id(),
                    args.username,
                    auth.hash_password(password),
                    actor=Principal("system"),
                )
            except ValueError as exc:
                _fail(str(exc))
            print(
                f"User '{args.username}' created. The API and web UI now require "
                "a login session (or the API key, if CPM_API_KEY is set)."
            )
        elif args.user_command == "passwd":
            password = _read_password(args)
            if not await storage.update_user_password(
                args.username, auth.hash_password(password), actor=Principal("system")
            ):
                _fail(f"No user named '{args.username}'.")
            print(f"Password updated for '{args.username}'.")
        elif args.user_command == "remove":
            try:
                deleted = await storage.delete_user(args.username, actor=Principal("system"))
            except ValueError as exc:
                _fail(str(exc))
            if not deleted:
                _fail(f"No user named '{args.username}'.")
            print(f"User '{args.username}' removed, along with any open sessions.")
            if await storage.count_users() == 0:
                print(
                    "No users remain: login is now disabled and the API is back to its API-key/open behaviour."
                )
        elif args.user_command == "list":
            users = await storage.list_users()
            if not users:
                print("No users. Create one with: camoufox-pm user add <name>")
            for user in users:
                print(f"{user['username']}\t(created {user['created_at']})")
    finally:
        await storage.close()


if __name__ == "__main__":
    main()
