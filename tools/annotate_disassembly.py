#!/usr/bin/env python3
"""Annotate disasm51 output with known EC/XRAM register semantics."""

import argparse
import pathlib
import re
from typing import NamedTuple


DPTR = re.compile(r"\bmov\s+DPTR,\s*#[A-Za-z0-9_]*?([0-9A-Fa-f]{4})\b", re.I)
LABEL = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):\s*$")


class Register(NamedTuple):
    start: int
    end: int
    name: str
    access: str
    meaning: str


def load_registers(path):
    registers = []
    for number, line in enumerate(pathlib.Path(path).read_text().splitlines(), 1):
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 4:
            raise ValueError(f"{path}:{number}: expected four tab-separated fields")
        bounds = fields[0].split("-", 1)
        registers.append(Register(int(bounds[0], 16), int(bounds[-1], 16), *fields[1:]))
    return registers


def find_register(registers, address):
    return next((item for item in registers if item.start <= address <= item.end), None)


def operation_after(lines, index):
    read = write = False
    for line in lines[index + 1:index + 9]:
        if DPTR.search(line):
            break
        lowered = line.lower()
        read |= bool(re.search(r"\bmovx\s+a,\s*@dptr", lowered))
        write |= bool(re.search(r"\bmovx\s+@dptr,\s*a", lowered))
    if read and write:
        return "read-modify-write"
    if read:
        return "read"
    if write:
        return "write"
    return "address passed/computed"


def analyze(text, registers, symbol_bias=1):
    """Annotate constant DPTR loads in a disasm51 listing.

    disasm51.py emits this firmware with ``org 0-1h`` and consequently names
    every ``dptr_NNNN`` symbol one byte below the immediate in the machine
    code. ``symbol_bias`` translates the printed symbol to the real address.
    """
    lines = text.splitlines()
    annotated = []
    references = []
    current_label = "(no label)"
    for index, line in enumerate(lines):
        label = LABEL.match(line)
        if label and not label.group(1).lower().startswith("dptr_"):
            current_label = label.group(1)
        match = DPTR.search(line)
        if match:
            printed_address = int(match.group(1), 16)
            address = (printed_address + symbol_bias) & 0xffff
            register = find_register(registers, address)
            if register:
                operation = operation_after(lines, index)
                suffix = (f"EC[0x{address:04X}] ec_{register.name}; "
                          f"{operation}; {register.meaning}")
                line += ("  " if ";" in line else "\t; ") + suffix
                references.append((address, register, current_label, index + 1, operation))
        annotated.append(line)
    return "\n".join(annotated) + ("\n" if text.endswith("\n") else ""), references


def write_xrefs(path, source, references):
    lines = [
        "# EC register references",
        "",
        f"Source: `{source}`",
        "",
        "Only constant `MOV DPTR,#address` references are listed. Computed DPTR accesses are not visible here.",
        "",
        "| EC/XRAM | Symbol | Operation | Nearest code label | Source line |",
        "|---:|---|---|---|---:|",
    ]
    for address, register, label, line, operation in references:
        lines.append(f"| `0x{address:04X}` | `ec_{register.name}` | {operation} | "
                     f"`{label}` | {line} |")
    pathlib.Path(path).write_text("\n".join(lines) + "\n")


def main(argv=None):
    root = pathlib.Path(__file__).parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="disasm51 .d52 input")
    parser.add_argument("-o", "--output", required=True, help="annotated .d52 output")
    parser.add_argument("--xrefs", help="optional Markdown cross-reference output")
    parser.add_argument("--registers", default=root / "ec_registers.tsv")
    parser.add_argument(
        "--symbol-bias", type=lambda value: int(value, 0), default=1,
        help="add this to printed dptr symbols (default: 1 for disasm51 org -1 output)")
    args = parser.parse_args(argv)

    source = pathlib.Path(args.input)
    annotated, references = analyze(source.read_text(errors="replace"),
                                    load_registers(args.registers), args.symbol_bias)
    pathlib.Path(args.output).write_text(annotated)
    if args.xrefs:
        write_xrefs(args.xrefs, source, references)
    print(f"annotated {len(references)} constant EC register references")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
