import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from infra_verifier import sandbox


class RuntimeLockDirTest(unittest.TestCase):
    """The guard allows the shared framework lock dirs and nothing else outside output."""

    def run_guarded(self, output, target):
        code = (
            "import pathlib,sys\n"
            "p=pathlib.Path(sys.argv[1])\n"
            "try: p.write_text('x'); p.unlink()\n"
            "except PermissionError: sys.exit(7)\n"
        )
        return subprocess.run(
            sandbox.command([sys.executable, "-c", code, str(target)], output),
            capture_output=True, timeout=20,
        ).returncode

    def test_lock_dir_writable_other_tmp_denied(self):
        try:
            sandbox.backend()
        except ValueError:
            self.skipTest("no native guard on this host")
        with tempfile.TemporaryDirectory() as d:
            output = Path(d, "output"); output.mkdir()
            lock_dir = Path(sandbox.runtime_lock_dirs()[0])
            self.assertTrue(lock_dir.name.startswith("openclaw-state-locks"))
            self.assertEqual(self.run_guarded(output, lock_dir / "probe.lock.sqlite"), 0)
            outside = Path("/tmp").resolve() / "infra-guard-outside-probe"
            self.assertEqual(self.run_guarded(output, outside), 7)
            self.assertFalse(outside.exists())

    def test_probe_reports_lock_dirs(self):
        try:
            sandbox.backend()
        except ValueError:
            self.skipTest("no native guard on this host")
        with tempfile.TemporaryDirectory() as d:
            output = Path(d, "output"); output.mkdir()
            info = sandbox.probe(output)
            self.assertEqual(info["runtime_lock_dirs"], sandbox.runtime_lock_dirs())


if __name__ == "__main__":
    unittest.main()
