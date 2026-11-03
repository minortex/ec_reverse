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


class MockI2EC:
    def __init__(self, memory=None):
        self.memory = memory or {}
        self.writes = []

    def read(self, address):
        return self.memory.get(address, 0)

    def write(self, address, value):
        self.writes.append((address, value))
        self.memory[address] = value


class CliTests(unittest.TestCase):
    def test_formatting_helpers(self):
        self.assertEqual(TOOL.describe_address(0x04AB), "0x04AB (1195)")
        self.assertEqual(TOOL.describe_byte(0x50), "0x50 (80)")
        self.assertEqual(TOOL.describe_word(0x4268), "0x4268 (17000)")
        self.assertEqual(TOOL.format_address(0x04AB), "0x04AB")
        self.assertEqual(TOOL.format_byte(0x50), "0x50")
        self.assertEqual(TOOL.format_word(0x4268), "0x4268")

    def test_read_single_byte(self):
        import contextlib
        import io
        from unittest import mock

        mock_i2ec = MockI2EC({0x04AB: 0x50})
        buf = io.StringIO()
        with mock.patch.object(TOOL, "open_i2ec", contextlib.contextmanager(lambda *a, **kw: (yield mock_i2ec))), \
             contextlib.redirect_stdout(buf):
            TOOL.main(["read", "0x04ab"])

        self.assertIn("XRAM[0x04AB (1195)] = 0x50 (80)", buf.getvalue())

    def test_read_two_bytes_word(self):
        import contextlib
        import io
        from unittest import mock

        mock_i2ec = MockI2EC({0x0439: 0x42, 0x0438: 0x68})
        buf = io.StringIO()
        with mock.patch.object(TOOL, "open_i2ec", contextlib.contextmanager(lambda *a, **kw: (yield mock_i2ec))), \
             contextlib.redirect_stdout(buf):
            TOOL.main(["read", "0x0439", "0x0438"])

        self.assertIn("XRAM[0x0439 (1081):0x0438 (1080)] = 0x4268 (17000)", buf.getvalue())

    def test_write_byte(self):
        import contextlib
        import io
        from unittest import mock

        mock_i2ec = MockI2EC({0x200D: 0x03, 0x0340: 0x00})
        buf = io.StringIO()
        with mock.patch.object(TOOL, "open_i2ec", contextlib.contextmanager(lambda *a, **kw: (yield mock_i2ec))), \
             contextlib.redirect_stdout(buf):
            TOOL.main(["write", "0x0340", "0x14"])

        self.assertIn("XRAM[0x0340 (832)] : 0x00 (0) -> 0x14 (20)", buf.getvalue())
        self.assertEqual(mock_i2ec.writes, [(0x0340, 0x14)])

    def test_dump_range(self):
        import contextlib
        import io
        from unittest import mock

        mock_i2ec = MockI2EC({0x0438: 0x68, 0x0439: 0x42})
        buf = io.StringIO()
        with mock.patch.object(TOOL, "open_i2ec", contextlib.contextmanager(lambda *a, **kw: (yield mock_i2ec))), \
             contextlib.redirect_stdout(buf):
            TOOL.main(["dump", "0x0438", "2"])

        output = buf.getvalue().splitlines()
        self.assertEqual(len(output), 2)
        self.assertIn("XRAM[0x0438 (1080)] = 0x68 (104)", output[0])
        self.assertIn("XRAM[0x0439 (1081)] = 0x42 (66)", output[1])

    def test_no_arguments_prints_help(self):
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            TOOL.main([])
        self.assertIn("usage:", buf.getvalue())


if __name__ == "__main__":
    unittest.main()

