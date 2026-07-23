#!/usr/bin/env python3
"""Build the official GXxHXxx 21.200 image with direct calibration commit and mode-locked voltage tiers."""

import argparse
import hashlib
from pathlib import Path


EXPECTED_SIZE = 0x40000
EXPECTED_SHA256 = "34c050d30772da07ef262fc7016e0677b9b1b4cdcd90cf43d93f0f15bf6a38c2"

CAL_OFFSET = 0x14974
CAL_EXPECTED = bytes.fromhex("90 03 9b")
CAL_REPLACEMENT = bytes.fromhex("02 c9 8e")

MODE_OFFSET = 0x1D303
MODE_EXPECTED = bytes.fromhex(
    "12 e7 c9 94 3d 50 1b c3 90 0a 4f e0 94 26 90 0a 4e "
    "e0 94 02 50 0c 90 0a 54 e0 ff"
)
MODE_REPLACEMENT = bytes.fromhex(
    "90 07 a6 e0 54 30 "
    "b4 20 05 7b fa 02 d3 ec "
    "b4 10 05 7b 96 02 d3 ec "
    "7b 00 02 d3 ec"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    if args.source.resolve() == args.output.resolve():
        raise SystemExit("refusing to overwrite source image")
    source = args.source.read_bytes()
    digest = hashlib.sha256(source).hexdigest()
    if len(source) != EXPECTED_SIZE or digest != EXPECTED_SHA256:
        raise SystemExit(f"refusing unknown image: size=0x{len(source):X}, sha256={digest}")

    checks = (
        (CAL_OFFSET, CAL_EXPECTED, CAL_REPLACEMENT),
        (MODE_OFFSET, MODE_EXPECTED, MODE_REPLACEMENT),
    )
    patched = bytearray(source)
    for offset, expected, replacement in checks:
        actual = bytes(patched[offset:offset + len(expected)])
        if actual != expected:
            raise SystemExit(
                f"original bytes differ at 0x{offset:05X}: {actual.hex(' ')}"
            )
        if len(expected) != len(replacement):
            raise SystemExit(f"internal patch length mismatch at 0x{offset:05X}")
        patched[offset:offset + len(replacement)] = replacement

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(patched)
    print(f"source sha256:  {digest}")
    print(f"patched sha256: {hashlib.sha256(patched).hexdigest()}")
    print("changed ranges: 0x14974-0x14976, 0x1D303-0x1D31D")
    print(f"wrote: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
