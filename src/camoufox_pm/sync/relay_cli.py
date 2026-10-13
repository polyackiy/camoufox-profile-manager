"""Separate relay launcher; never opens the manager's database or browser."""

from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path

from camoufox_pm.core.update_permissions import (
    create_private_file,
    reject_reparse_paths,
    verify_private_path,
)


def read_registration_token(path: Path) -> str:
    """Read a bounded private operator credential; never accept a command-line secret."""
    import stat

    reject_reparse_paths(path)
    verify_private_path(path)
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    )
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 1024:
            raise ValueError("Invalid registration credential")
        token = stream.read(1025).decode("ascii").strip()
        if not 32 <= len(token) <= 512 or any(c.isspace() for c in token):
            raise ValueError("Invalid registration credential")
        return token


def main() -> None:
    parser = argparse.ArgumentParser(description="Self-hosted encrypted Camoufox PM sync relay")
    parser.add_argument("--data", type=Path, default=Path("sync-relay-data"))
    parser.add_argument(
        "--expire-uploads",
        action="store_true",
        help="Expire unpublished uploads older than seven days",
    )
    parser.add_argument(
        "--collect-retained",
        action="store_true",
        help="Execute only delayed owner-signed ciphertext retention decisions",
    )
    parser.add_argument("--maintenance-limit", type=int, default=100)
    parser.add_argument("--origin", help="Public HTTPS origin, e.g. https://sync.example.org")
    parser.add_argument("--registration-token-file", type=Path)
    parser.add_argument("--generate-registration-token", type=Path, metavar="NEW_FILE")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9877)
    parser.add_argument("--development-loopback", action="store_true")
    args = parser.parse_args()
    if args.expire_uploads and args.collect_retained:
        parser.error("Choose one maintenance operation")
    if args.collect_retained:
        if args.generate_registration_token or not 1 <= args.maintenance_limit <= 1000:
            parser.error("Maintenance requires limit 1..1000 and no token generation")
        try:
            from .relay_retention import collect_retained_ciphertext

            result = collect_retained_ciphertext(args.data, limit=args.maintenance_limit)
        except Exception:
            parser.exit(
                1, "Authorized retention maintenance failed; incomplete cleanup retains quota.\n"
            )
        print(
            "Retention maintenance: " + ", ".join(f"{key}={value}" for key, value in result.items())
        )
        return
    if args.expire_uploads:
        if args.generate_registration_token or not 1 <= args.maintenance_limit <= 1000:
            parser.error("Maintenance requires limit 1..1000 and no token generation")
        try:
            from camoufox_pm.sync.relay import expire_uncommitted_uploads

            result = expire_uncommitted_uploads(args.data, limit=args.maintenance_limit)
        except Exception:
            parser.exit(
                1, "Relay upload maintenance failed; incomplete cleanup retains reservations.\n"
            )
        print("Upload maintenance: " + ", ".join(f"{key}={value}" for key, value in result.items()))
        return
    if args.generate_registration_token:
        try:
            with os.fdopen(create_private_file(args.generate_registration_token), "wb") as output:
                output.write((secrets.token_urlsafe(32) + "\n").encode("ascii"))
                output.flush()
                os.fsync(output.fileno())
        except Exception:
            parser.exit(1, "Could not create a new private registration token file.\n")
        print("Private registration token file created. Keep it outside the relay data directory.")
        return
    if not args.origin or not args.registration_token_file or not 1 <= args.port <= 65535:
        parser.error("--origin and --registration-token-file are required; use a valid port")
    if args.development_loopback and args.host not in {"127.0.0.1", "::1", "localhost"}:
        parser.error("Development HTTP may bind only to loopback")
    try:
        import uvicorn
        from pydantic import SecretStr

        from camoufox_pm.sync.relay import create_relay_app

        app = create_relay_app(
            args.data,
            relay_url=args.origin,
            registration_token=SecretStr(read_registration_token(args.registration_token_file)),
            development_loopback=args.development_loopback,
        )
    except ImportError:
        parser.exit(1, "Install camoufox-profile-manager[sync] to run the relay.\n")
    except Exception:
        parser.exit(1, "Relay configuration or protected storage is invalid.\n")
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        access_log=False,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1,::1",
    )


if __name__ == "__main__":
    main()
