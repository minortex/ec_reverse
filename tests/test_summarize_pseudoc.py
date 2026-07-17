import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "summarize_pseudoc", ROOT / "tools" / "summarize_pseudoc.py")
TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOL)


class SummarizePseudoCTests(unittest.TestCase):
    def test_indexes_only_functions_with_named_registers(self):
        text = """/* CODE:CODE:e136 is_user_fan_mode */
byte is_user_fan_mode(void) { return ec_fan_power_mode & 0x80; }
/* CODE:CODE:e13d FUN_CODE_e13d */
void FUN_CODE_e13d(void) { UNK_EXTMEM_1234 = 1; }
"""
        result = TOOL.analyze(text)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "is_user_fan_mode")
        self.assertEqual(result[0]["domains"], ["fan", "power"])


if __name__ == "__main__":
    unittest.main()
