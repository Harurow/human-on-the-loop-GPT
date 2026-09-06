import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("installer", ROOT / "scripts/install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallTests(unittest.TestCase):
    def test_copy_runs_without_distribution_and_pm_is_optional(self):
        with tempfile.TemporaryDirectory(prefix="hotl install ") as folder:
            result = installer.install(folder)
            self.assertEqual(len(result["installed"]), 1)
            base = Path(folder) / ".agents/skills"
            self.assertFalse((base / "hotl-gpt-pm").exists())
            run = subprocess.run([sys.executable, str(base / "hotl-gpt/scripts/hotl.py"),
                                  "--project", folder, "init", "--input", "-"],
                                 input=b'{"request":"standalone installed copy"}', capture_output=True)
            self.assertEqual(run.returncode, 0, run.stderr)

    def test_force_preserves_existing_install_in_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            installer.install(folder)
            target = Path(folder) / ".agents/skills/hotl-gpt/user-note.txt"
            target.write_text("Keep this")
            with self.assertRaises(ValueError):
                installer.install(folder, pm=True)
            self.assertFalse((target.parents[1] / "hotl-gpt-pm").exists())
            result = installer.install(folder, pm=True, force=True)
            backup = Path(result["installed"][0]["backup"])
            self.assertEqual((backup / "user-note.txt").read_text(), "Keep this")
            self.assertTrue((target.parents[1] / "hotl-gpt-pm/SKILL.md").is_file())

    def test_link_repeat_is_noop_and_symlink_parent_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            installer.install(folder, link=True)
            self.assertEqual(installer.install(folder, link=True)["installed"][0]["status"], "already linked")
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "target"
            target.mkdir()
            external = Path(folder) / "external"
            external.mkdir()
            (target / ".agents").symlink_to(external)
            with self.assertRaises(ValueError):
                installer.install(target)
            self.assertEqual(list(external.iterdir()), [])

    def test_shell_entry_unknown_argument_and_missing_target(self):
        result = subprocess.run(["bash", str(ROOT / "install.sh"), "--unknown"], capture_output=True)
        self.assertEqual(result.returncode, 2)
        with self.assertRaises(ValueError):
            installer.install("/tmp/hotl-definitely-missing-target-854783476")


if __name__ == "__main__":
    unittest.main()
