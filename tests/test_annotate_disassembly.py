import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "annotate_disassembly", ROOT / "tools" / "annotate_disassembly.py")
TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOL)


class AnnotateDisassemblyTests(unittest.TestCase):
    def test_known_dptr_read_write_is_annotated(self):
        source = """jump_C5B3:
\tmov DPTR, #dptr_0725
\tmovx A, @DPTR
\tanl A, #01h
\tmovx @DPTR, A
\tret
"""
        registers = TOOL.load_registers(ROOT / "tools" / "ec_registers.tsv")
        output, references = TOOL.analyze(source, registers)
        self.assertIn("ec_ap_oem_9", output)
        self.assertIn("read-modify-write", output)
        self.assertEqual(references[0][2], "jump_C5B3")
        self.assertEqual(references[0][0], 0x0726)

    def test_disasm51_org_minus_one_symbol_is_corrected(self):
        source = "\tmov DPTR, #dptr_0522\n\tmovx A, @DPTR\n"
        registers = TOOL.load_registers(ROOT / "tools" / "ec_registers.tsv")
        output, references = TOOL.analyze(source, registers)
        self.assertIn("EC[0x0523] ec_charge_voltage_limit", output)
        self.assertEqual(references[0][0], 0x0523)

    def test_code_label_alone_is_not_annotated(self):
        source = "dptr_0751:\n\tret\n"
        registers = TOOL.load_registers(ROOT / "tools" / "ec_registers.tsv")
        output, references = TOOL.analyze(source, registers)
        self.assertEqual(output, source)
        self.assertEqual(references, [])


if __name__ == "__main__":
    unittest.main()
