"""Version selection after an in-place desktop upgrade, without app dependencies."""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class FrozenVersionTests(unittest.TestCase):
    def version_hook_module(self):
        spec = importlib.util.spec_from_file_location(
            "build_version", ROOT / "packaging/build_version.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def resolve_version(self, frozen, stamped):
        with tempfile.TemporaryDirectory() as temporary:
            metadata = Path(temporary) / "camoufox_profile_manager-0.6.0rc7.dist-info"
            metadata.mkdir()
            (metadata / "METADATA").write_text(
                "Name: camoufox-profile-manager\nVersion: 0.6.0rc7\n", encoding="utf-8"
            )
            hook = Path(temporary) / "runtime_hook.py"
            self.version_hook_module().write_runtime_version_hook(hook, stamped)
            code = (
                "import json,runpy,sys\n"
                f"sys.path.insert(0, {temporary!r})\n"
                f"sys.frozen = {frozen!r}\n"
                f"runpy.run_path({str(hook)!r})\n"
                f"value=runpy.run_path({str(ROOT / 'src/camoufox_pm/__init__.py')!r})\n"
                "print(json.dumps(value['__version__']))\n"
            )
            result = subprocess.run(
                [sys.executable, "-c", code], check=True, capture_output=True, text=True
            )
            return json.loads(result.stdout)

    def test_frozen_build_version_wins_over_older_installed_metadata(self):
        self.assertEqual(self.resolve_version(True, "0.6.0rc8"), "0.6.0rc8")

    def test_source_uses_installed_metadata_even_if_stamp_attribute_exists(self):
        self.assertEqual(self.resolve_version(False, "0.6.0rc8"), "0.6.0rc7")

    def test_other_frozen_entrypoints_retain_metadata_fallback(self):
        self.assertEqual(self.resolve_version(True, None), "0.6.0rc7")

    def test_missing_distribution_keeps_unknown_source_version(self):
        code = (
            "import json,runpy\n"
            "from importlib.metadata import PackageNotFoundError\n"
            "from unittest.mock import patch\n"
            "with patch('importlib.metadata.version', side_effect=PackageNotFoundError):\n"
            f" value=runpy.run_path({str(ROOT / 'src/camoufox_pm/__init__.py')!r})\n"
            "print(json.dumps(value['__version__']))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], check=True, capture_output=True, text=True
        )
        self.assertEqual(json.loads(result.stdout), "0.0.0+unknown")

    def test_generated_runtime_hook_embeds_a_literal_and_is_registered(self):
        module = self.version_hook_module()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "nested" / "version_hook.py"
            value = "0.6.0rc8'; raise RuntimeError('not code')\n"
            module.write_runtime_version_hook(path, value)
            code = (
                f"import runpy,sys; runpy.run_path({str(path)!r}); "
                "print(repr(sys._camoufox_pm_build_version))"
            )
            result = subprocess.run(
                [sys.executable, "-c", code], check=True, capture_output=True, text=True
            )
            self.assertEqual(result.stdout.strip(), repr(value))
        source = (ROOT / "packaging/camoufox-pm.spec").read_text(encoding="utf-8")
        self.assertIn('version("camoufox-profile-manager")', source)
        self.assertIn("runtime_hooks=[str(version_hook)]", source)


if __name__ == "__main__":
    unittest.main()
