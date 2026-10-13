"""Native packaging smoke: recovery verification must run in a separate process."""

from __future__ import annotations

import sys

from camoufox_pm.sync.recovery import RECOVERY_WORKER_FLAG, recovery_worker_main

if sys.argv[1:] == [RECOVERY_WORKER_FLAG]:
    raise SystemExit(recovery_worker_main())

from camoufox_pm.sync.identity import bootstrap  # noqa: E402
from camoufox_pm.sync.recovery import create_recovery, verify_recovery_isolated  # noqa: E402


def main() -> None:
    root, secrets, _membership = bootstrap("https://relay.example")
    package, recovery_secret = create_recovery(root, secrets.authority)
    del secrets
    verify_recovery_isolated(package, recovery_secret, root)
    if sys.stdout is not None:
        print("Isolated offline owner recovery passed")


if __name__ == "__main__":
    main()
