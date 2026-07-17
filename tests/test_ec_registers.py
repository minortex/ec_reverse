import pathlib
import unittest

ROOT = pathlib.Path(__file__).parents[1]


class EcRegisterMapTests(unittest.TestCase):
    def test_register_ranges_are_valid_and_non_overlapping(self):
        ranges = []
        path = ROOT / "tools" / "ec_registers.tsv"
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if not line or line.startswith("#"):
                continue
            fields = line.split("\t")
            self.assertEqual(len(fields), 4, f"line {number}")
            bounds = fields[0].split("-")
            start = int(bounds[0], 16)
            end = int(bounds[-1], 16)
            self.assertLessEqual(start, end)
            self.assertLessEqual(end, 0xFFFF)
            ranges.append((start, end, fields[1]))

        for previous, current in zip(ranges, ranges[1:]):
            self.assertLess(previous[1], current[0],
                            f"{previous[2]} overlaps or is out of order with {current[2]}")

    def test_key_control_registers_are_present(self):
        text = (ROOT / "tools" / "ec_registers.tsv").read_text()
        for row in ("0751\tfan_power_mode", "07a6\tbattery_mode",
                    "0f00-0f0f\tcpu_fan_temp_up", "0f5f\tfan_table_control"):
            self.assertIn(row, text)


if __name__ == "__main__":
    unittest.main()
