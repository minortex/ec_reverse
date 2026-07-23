#!/usr/bin/env python3
"""Build the official image while ignoring ApExistFlag's mode-clear action in D1D1."""

import argparse
import hashlib
from pathlib import Path

EXPECTED_SIZE = 0x40000
EXPECTED_SHA256 = "34c050d30772da07ef262fc7016e0677b9b1b4cdcd90cf43d93f0f15bf6a38c2"

# bank1:D1D7: LCALL DF7E; JNZ skip_mode_clear
OFFSET = 0x1D1D7
EXPECTED = bytes.fromhex("12 df 7e 70 07")
# Always skip the seven-byte MOVX/ANL/MOVX mode-bit clearing block at D1DC-D1E2.
REPLACEMENT = bytes.fromhex("80 0a 00 00 00")


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
    actual = source[OFFSET:OFFSET + len(EXPECTED)]
    if actual != EXPECTED:
        raise SystemExit(f"original bytes differ at 0x{OFFSET:05X}: {actual.hex(' ')}")
    patched = bytearray(source)
    patched[OFFSET:OFFSET + len(REPLACEMENT)] = REPLACEMENT
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(patched)
    print(f"source sha256:  {digest}")
    print(f"patched sha256: {hashlib.sha256(patched).hexdigest()}")
    print("changed range:  0x1D1D7-0x1D1DB")
    print(f"wrote: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
