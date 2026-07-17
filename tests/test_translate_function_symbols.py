import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "translate_function_symbols", ROOT / "tools" / "translate_function_symbols.py")
TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOL)


class TranslateFunctionSymbolsTests(unittest.TestCase):
    def test_unique_short_function_moves(self):
        function = b"\x90\x07\x51\xe0\x54\x80\x22"
        reference = b"x" * 10 + function + b"z"
        target = b"q" * 30 + function + b"r"
        rows = [("000a", "is_user_fan_mode", "high", "evidence")]
        translated, rejected = TOOL.translate(reference, target, rows)
        self.assertEqual(translated[0][0], "001e")
        self.assertEqual(rejected, [])

    def test_ambiguous_signature_is_rejected(self):
        function = b"\x90\x07\x51\xe0\x22"
        rows = [("0000", "accessor", "high", "evidence")]
        translated, rejected = TOOL.translate(function, function * 2, rows)
        self.assertEqual(translated, [])
        self.assertEqual(rejected[0][1], "2 target matches")


if __name__ == "__main__":
    unittest.main()
