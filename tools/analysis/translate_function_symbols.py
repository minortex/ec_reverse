#!/usr/bin/env python3
"""Translate short 8051 function symbols between firmware versions by bytes."""

import argparse
import pathlib
import sys


RET = 0x22


def signature(data, address, maximum=64):
    end = min(len(data), address + maximum)
    try:
        ret = data.index(RET, address, end)
    except ValueError:
        return None
    value = data[address:ret + 1]
    return value if len(value) >= 5 else None


def occurrences(data, needle):
    found = []
    start = 0
    while True:
        offset = data.find(needle, start)
        if offset < 0:
            return found
        found.append(offset)
        start = offset + 1


def translate(reference, target, rows):
    translated = []
    rejected = []
    for row in rows:
        address = int(row[0], 16)
        pattern = signature(reference, address)
        matches = [] if pattern is None else occurrences(target, pattern)
        if len(matches) == 1:
            translated.append((f"{matches[0]:04x}", *row[1:]))
        else:
            rejected.append((row[1], "no signature" if pattern is None
                             else f"{len(matches)} target matches"))
    return translated, rejected


def read_rows(path):
    rows = []
    for number, line in enumerate(pathlib.Path(path).read_text().splitlines(), 1):
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 4:
            raise ValueError(f"{path}:{number}: expected four fields")
        rows.append(tuple(fields))
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference_image")
    parser.add_argument("target_image")
    parser.add_argument("-s", "--symbols", default=pathlib.Path(__file__).parent / "ec_functions.tsv")
    parser.add_argument("-o", "--output", required=True)
    args = parser.parse_args(argv)

    translated, rejected = translate(pathlib.Path(args.reference_image).read_bytes(),
                                     pathlib.Path(args.target_image).read_bytes(),
                                     read_rows(args.symbols))
    header = ("# Translated by exact function bytes from " + args.reference_image + "\n" +
              "# address<TAB>name<TAB>confidence<TAB>evidence\n")
    pathlib.Path(args.output).write_text(
        header + "".join("\t".join(row) + "\n" for row in translated))
    for name, reason in rejected:
        print(f"not translated: {name}: {reason}", file=sys.stderr)
    print(f"translated {len(translated)} symbols; rejected {len(rejected)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
