"""Exercise mandatory Windows byte locks with a real competing process.

Run with unittest and only uvicorn installed; no browser or profile is needed.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from camoufox_pm.desktop import _instance_lock


class DesktopLockProcessTests(unittest.TestCase):
    def test_competing_process_gets_already_running_then_can_reopen(self):
        source = str(Path(__file__).resolve().parents[2] / "src")
        env = {
            **os.environ,
            "PYTHONPATH": os.pathsep.join((source, os.environ.get("PYTHONPATH", ""))),
        }
        probe = """
import sys
from pathlib import Path
from camoufox_pm.desktop import AlreadyRunningError, _instance_lock
try:
    with _instance_lock(Path(sys.argv[1])):
        print('acquired')
except AlreadyRunningError as exc:
    print(str(exc))
    sys.exit(23)
"""
        with tempfile.TemporaryDirectory() as directory:

            def attempt():
                return subprocess.run(
                    [sys.executable, "-c", probe, directory],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )

            with _instance_lock(Path(directory)):
                blocked = attempt()
                self.assertEqual(blocked.returncode, 23, blocked.stderr)
                self.assertIn("already open", blocked.stdout)
                self.assertNotIn("PermissionError", blocked.stderr)
            reopened = attempt()
            self.assertEqual(reopened.returncode, 0, reopened.stderr)
            self.assertEqual(reopened.stdout.strip(), "acquired")


if __name__ == "__main__":
    unittest.main()
