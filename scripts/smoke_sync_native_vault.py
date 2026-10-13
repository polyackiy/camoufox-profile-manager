"""CI-only native credential backend proof with a disposable isolated namespace."""

import sys
from uuid import uuid4

from camoufox_pm.sync.vault import OSVault


def main():
    if sys.platform not in {"darwin", "win32"}:
        raise SystemExit("Native credential smoke requires macOS or Windows CI")
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
