"""CI-only native credential backend proof with a disposable isolated namespace."""

import sys
from uuid import uuid4

from camoufox_pm.sync.vault import OSVault, VaultError


def main():
    if sys.platform not in {"darwin", "win32", "linux"}:
        raise SystemExit("Native credential smoke requires macOS, Windows or Linux CI")
    if sys.argv[1:] == ["--unavailable"]:
        try:
            OSVault(str(uuid4())).put(str(uuid4()), b"disposable-unavailable-test")
        except VaultError:
            print("Unavailable native storage rejected without fallback")
            return
        raise AssertionError("Unavailable native storage unexpectedly accepted a write")
    vault = OSVault(str(uuid4()))
    slot = str(uuid4())
    try:
        vault.put(slot, b"camoufox-sync-disposable-platform-test")
        assert vault.get(slot) == b"camoufox-sync-disposable-platform-test"
    finally:
        vault.delete(slot)
    assert vault.get(slot) is None
    print("Native protected sync storage roundtrip and cleanup passed")


if __name__ == "__main__":
    main()
