#!/usr/bin/env python3
"""Recover Keil C51 bank-wrapper targets from the common EC code image."""

import argparse
import json
import pathlib


THUNKS = {0x00: "bank0", 0x14: "bank1", 0x28: "bank2"}


def extract(data):
    result = {name: [] for name in THUNKS.values()}
    for offset in range(len(data) - 5):
        # MOV DPTR,#target ; LJMP 0x11xx
        if data[offset] != 0x90 or data[offset + 3:offset + 5] != b"\x02\x11":
            continue
        bank = THUNKS.get(data[offset + 5])
        if bank is None:
            continue
        target = (data[offset + 1] << 8) | data[offset + 2]
        result[bank].append({"wrapper": offset, "target": target})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("common_image", help="64-KiB image containing the 0x1100 thunks")
    parser.add_argument("--entries", choices=THUNKS.values(),
                        help="print a comma-separated Ghidra entry list")
    parser.add_argument("-o", "--output", help="write JSON report instead of stdout")
    args = parser.parse_args(argv)

    path = pathlib.Path(args.common_image)
    result = extract(path.read_bytes())
    if args.entries:
        targets = sorted({item["target"] for item in result[args.entries]})
        print(",".join(f"0x{target:04x}" for target in targets))
        return 0

    report = {
        "format_version": 1,
        "source": str(path),
        "thunks": {"bank0": "0x1100", "bank1": "0x1114", "bank2": "0x1128"},
        "banks": result,
    }
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        pathlib.Path(args.output).write_text(text)
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
