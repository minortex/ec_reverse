import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("i2ec_rw", ROOT / "tools/i2ec_rw.py")
TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOL)


class FakePort:
    def __init__(self, read_value=0):
        self.read_value = read_value
        self.writes = []
        self.reads = []

    def write_byte(self, port, value):
        self.writes.append((port, value))

    def read_byte(self, port):
        self.reads.append(port)
        return self.read_value


class I2ECTests(unittest.TestCase):
    def test_read_selects_high_then_low_address(self):
        port = FakePort(0x5A)
        i2ec = TOOL.I2EC(port)

        self.assertEqual(i2ec.read(0x09C9), 0x5A)
        self.assertEqual(port.writes, [(0x681, 0x09), (0x682, 0xC9)])
        self.assertEqual(port.reads, [0x683])

    def test_write_selects_address_before_data(self):
        port = FakePort()
        i2ec = TOOL.I2EC(port)

        i2ec.write(0xA075, 0xEE)
        self.assertEqual(
            port.writes, [(0x681, 0xA0), (0x682, 0x75), (0x683, 0xEE)]
        )

    def test_custom_base_port(self):
        port = FakePort(0x12)
        i2ec = TOOL.I2EC(port, 0x300)

        i2ec.read(0xFFFF)
        self.assertEqual(port.writes, [(0x301, 0xFF), (0x302, 0xFF)])
        self.assertEqual(port.reads, [0x303])

    def test_rejects_out_of_range_values(self):
        i2ec = TOOL.I2EC(FakePort())

        with self.assertRaises(ValueError):
            i2ec.read(0x10000)
        with self.assertRaises(ValueError):
            i2ec.write(0, 0x100)

    def test_rejects_unaligned_base_port(self):
        with self.assertRaises(ValueError):
            TOOL.I2EC(FakePort(), 0x681)
        with self.assertRaises(ValueError):
            TOOL.I2EC(FakePort(), 0x1000)


if __name__ == "__main__":
    unittest.main()
