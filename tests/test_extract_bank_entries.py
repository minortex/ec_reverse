import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "extract_bank_entries", ROOT / "tools" / "extract_bank_entries.py")
TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOL)


class ExtractBankEntriesTests(unittest.TestCase):
    def test_extracts_only_known_complete_wrappers(self):
        data = (b"x\x90\x9c\xc3\x02\x11\x14" +
                b"\x90\x87\x00\x02\x11\x28" +
                b"\x90\xaa\xbb\x02\x11\x99" +
                b"\x90\x12")
        result = TOOL.extract(data)
        self.assertEqual(result["bank1"], [{"wrapper": 1, "target": 0x9CC3}])
        self.assertEqual(result["bank2"], [{"wrapper": 7, "target": 0x8700}])
        self.assertEqual(result["bank0"], [])


if __name__ == "__main__":
    unittest.main()
