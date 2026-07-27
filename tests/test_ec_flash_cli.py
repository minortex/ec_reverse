import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
EC_FLASH = ROOT / "build" / "ec-flash"
BLOCK_SIZE = 0x10000


class EcFlashCliTests(unittest.TestCase):
    def run_ec_flash(self, *args):
        return subprocess.run(
            [os.fspath(EC_FLASH), *map(os.fspath, args)],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

    def make_image(self, directory, size):
        path = Path(directory) / "image.bin"
        path.write_bytes(b"\xff" * size)
        return path

    def test_write_rejects_unaligned_file_before_hardware_access(self):
        with tempfile.TemporaryDirectory() as directory:
            image = self.make_image(directory, 1)
            result = self.run_ec_flash("--write", image)
        self.assertEqual(result.returncode, 1)
        self.assertIn("must both be aligned", result.stderr)
        self.assertNotIn("iopl(3)", result.stderr)

    def test_write_rejects_unaligned_address_before_hardware_access(self):
        with tempfile.TemporaryDirectory() as directory:
            image = self.make_image(directory, BLOCK_SIZE)
            result = self.run_ec_flash("--write", image, "--address", "1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("must both be aligned", result.stderr)
        self.assertNotIn("iopl(3)", result.stderr)

    def test_write_rejects_explicit_length_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            image = self.make_image(directory, BLOCK_SIZE)
            result = self.run_ec_flash(
                "--write", image, "--length", hex(BLOCK_SIZE * 2)
            )
        self.assertEqual(result.returncode, 1)
        self.assertIn("write file is", result.stderr)
        self.assertNotIn("iopl(3)", result.stderr)

    def test_write_is_mutually_exclusive_with_dump_and_verify(self):
        for other_option in ("--dump", "--verify"):
            with self.subTest(other_option=other_option):
                result = self.run_ec_flash(
                    "--write", "unused.bin", other_option, "unused-output.bin"
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn("Usage:", result.stderr)
                self.assertNotIn("iopl(3)", result.stderr)

    def test_write_rejects_empty_file_before_hardware_access(self):
        with tempfile.TemporaryDirectory() as directory:
            image = self.make_image(directory, 0)
            result = self.run_ec_flash("--write", image)
        self.assertEqual(result.returncode, 1)
        self.assertIn("write file size 0 is invalid", result.stderr)
        self.assertNotIn("iopl(3)", result.stderr)

    def test_help_describes_default_shutdown_reset(self):
        result = self.run_ec_flash("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("one-shot EC reset at final system shutdown", result.stdout)
        self.assertIn("no immediate reset is sent", result.stdout)
        self.assertIn("--reset-only", result.stdout)
        self.assertNotIn("  --reset           ", result.stdout)
        self.assertNotIn("--reset-on-shutdown", result.stdout)

    def test_removed_reset_options_are_rejected_before_hardware_access(self):
        for option in ("--reset", "--reset-on-shutdown"):
            with self.subTest(option=option):
                result = self.run_ec_flash(option)
                self.assertEqual(result.returncode, 2)
                self.assertIn("Usage:", result.stderr)
                self.assertNotIn("iopl(3)", result.stderr)

    def test_reset_only_rejects_other_actions_before_hardware_access(self):
        result = self.run_ec_flash("--reset-only", "--write", "unused.bin")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Usage:", result.stderr)
        self.assertNotIn("iopl(3)", result.stderr)

    @unittest.skipIf(os.geteuid() == 0, "non-root behavior cannot be tested as root")
    def test_aligned_write_rejects_non_root_before_three_second_delay(self):
        with tempfile.TemporaryDirectory() as directory:
            image = self.make_image(directory, BLOCK_SIZE)
            result = self.run_ec_flash("--write", image)
        self.assertEqual(result.returncode, 1)
        self.assertIn("must be run as root", result.stderr)
        self.assertNotIn("EC write starts in", result.stderr)


if __name__ == "__main__":
    unittest.main()
