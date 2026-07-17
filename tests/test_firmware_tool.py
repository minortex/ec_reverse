import importlib.util
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("firmware_tool", ROOT / "tools/firmware_tool.py")
TOOL = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(TOOL)

class FirmwareToolTests(unittest.TestCase):
    def test_analysis_blocks_sum_and_runs(self):
        data = b"ABCDEF" + b"\xff" * 300 + b"Z" * (TOOL.BLOCK - 306) + b"Q"
        result = TOOL.analyze("sample.bin", data)
        self.assertEqual(result["byte_sum"], sum(data))
        self.assertEqual(len(result["blocks"]), 2)
        self.assertEqual(result["ff_runs"][0], {"start": 6, "end": 305, "length": 300})
        self.assertEqual(result["ascii_strings"][0]["value"], "ABCDEF")
        self.assertTrue(result["warnings"])

    def test_expected_layout_and_vectors(self):
        data = bytearray(b"\xff" * (4 * TOOL.BLOCK))
        data[0:3] = b"\x02\x00\x70"
        data[0x20000:0x20003] = b"\x02\x83\xb7"
        result = TOOL.analyze("ec.bin", bytes(data))
        self.assertEqual(result["warnings"], [])
        self.assertEqual([v["target"] for v in result["entry_vectors"]], [0x70, 0x83B7])

    def test_diff_clusters_and_length_change(self):
        clusters = TOOL.diff_clusters(b"abcdef", b"abXYef!")
        self.assertEqual([(c["start"], c["end"]) for c in clusters], [(2, 3), (6, 6)])

    def test_split_round_trip(self):
        data = bytes(range(256)) * 300
        with tempfile.TemporaryDirectory() as tmp:
            image = pathlib.Path(tmp) / "fw.bin"; out = pathlib.Path(tmp) / "out"
            image.write_bytes(data); TOOL.main(["split", str(image), str(out)])
            joined = b"".join(p.read_bytes() for p in sorted(out.glob("block*.bin")))
            self.assertEqual(joined, data)

if __name__ == "__main__": unittest.main()
