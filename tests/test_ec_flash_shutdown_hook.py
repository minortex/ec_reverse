import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "ec-flash" / "ec-flash-system-shutdown"


class EcFlashShutdownHookTests(unittest.TestCase):
    def run_hook(self, action, marker, fake_ec_flash, output):
        env = os.environ.copy()
        env.update(
            EC_FLASH_RESET_MARKER=os.fspath(marker),
            EC_FLASH_BIN=os.fspath(fake_ec_flash),
            EC_FLASH_TEST_OUTPUT=os.fspath(output),
        )
        return subprocess.run(
            [os.fspath(HOOK), action],
            cwd=ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

    def make_fake_ec_flash(self, directory):
        path = Path(directory) / "ec-flash"
        path.write_text(
            '#!/bin/sh\nprintf "%s\\n" "$*" >"$EC_FLASH_TEST_OUTPUT"\n'
        )
        path.chmod(0o755)
        return path

    def test_no_marker_is_a_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            fake = self.make_fake_ec_flash(directory)
            marker = directory / "missing-marker"
            output = directory / "called"
            result = self.run_hook("poweroff", marker, fake, output)
            self.assertEqual(result.returncode, 0)
            self.assertFalse(output.exists())

    def test_poweroff_consumes_marker_and_requests_reset_only(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            fake = self.make_fake_ec_flash(directory)
            marker = directory / "marker"
            marker.write_text("armed\n")
            output = directory / "called"
            result = self.run_hook("poweroff", marker, fake, output)
            self.assertEqual(result.returncode, 0)
            self.assertFalse(marker.exists())
            self.assertEqual(output.read_text(), "--reset-only\n")
            self.assertIn("sending EC 0xfe before poweroff", result.stderr)

    def test_kexec_does_not_consume_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            fake = self.make_fake_ec_flash(directory)
            marker = directory / "marker"
            marker.write_text("armed\n")
            output = directory / "called"
            result = self.run_hook("kexec", marker, fake, output)
            self.assertEqual(result.returncode, 0)
            self.assertTrue(marker.exists())
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
