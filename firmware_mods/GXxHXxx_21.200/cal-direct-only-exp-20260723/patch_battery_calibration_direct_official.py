#!/usr/bin/env python3
"""Patch only direct battery-calibration commit into the official firmware."""

import argparse
import hashlib
from pathlib import Path


EXPECTED_SIZE = 0x40000
EXPECTED_SHA256 = "34c050d30772da07ef262fc7016e0677b9b1b4cdcd90cf43d93f0f15bf6a38c2"
PATCH_OFFSET = 0x14974
EXPECTED_BYTES = bytes.fromhex("90 03 9b")
REPLACEMENT_BYTES = bytes.fromhex("02 c9 8e")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    if args.source.resolve() == args.output.resolve():
        raise SystemExit("refusing to overwrite the source image")

    source = args.source.read_bytes()
    digest = hashlib.sha256(source).hexdigest()
    if len(source) != EXPECTED_SIZE or digest != EXPECTED_SHA256:
        raise SystemExit(
            f"refusing unknown image: size=0x{len(source):X}, sha256={digest}"
        )

    actual = source[PATCH_OFFSET : PATCH_OFFSET + len(EXPECTED_BYTES)]
    if actual != EXPECTED_BYTES:
        raise SystemExit(
            f"original bytes differ at 0x{PATCH_OFFSET:05X}: {actual.hex(' ')}"
        )

    patched = bytearray(source)
    patched[PATCH_OFFSET : PATCH_OFFSET + len(EXPECTED_BYTES)] = REPLACEMENT_BYTES

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(patched)
    print(f"source sha256:  {digest}")
    print(f"patched sha256: {hashlib.sha256(patched).hexdigest()}")
    print(f"wrote: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
