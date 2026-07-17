import pathlib
import unittest

ROOT = pathlib.Path(__file__).parents[1]


class EcFunctionSymbolsTests(unittest.TestCase):
    def test_function_symbols_are_unique_and_well_formed(self):
        for filename in ("ec_functions.tsv", "ec_functions-main0.tsv"):
            addresses = set()
            names = set()
            for number, line in enumerate(
                    (ROOT / "tools" / filename).read_text().splitlines(), 1):
                if not line or line.startswith("#"):
                    continue
                fields = line.split("\t")
                self.assertEqual(len(fields), 4, f"{filename} line {number}")
                address, name, confidence, evidence = fields
                self.assertNotIn(address, addresses, filename)
                self.assertNotIn(name, names, filename)
                self.assertLessEqual(int(address, 16), 0xFFFF)
                self.assertIn(confidence, {"high", "medium", "low"})
                self.assertTrue(evidence)
                addresses.add(address)
                names.add(name)


if __name__ == "__main__":
    unittest.main()
