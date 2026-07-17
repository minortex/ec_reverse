#!/usr/bin/env python3
"""Build a semantic function index from ExportReadableEc pseudo-C."""

import argparse
import pathlib
import re


FUNCTION = re.compile(
    r"/\* CODE:CODE:([0-9a-fA-F]+)\s+([^ ]+) \*/\s*(.*?)(?=/\* CODE:CODE:|\Z)", re.S)
EC_SYMBOL = re.compile(r"\bec_[a-zA-Z0-9_]+\b")


def domains(symbols):
    result = []
    joined = " ".join(symbols)
    groups = [
        ("fan", ("fan",)),
        ("battery", ("battery", "charge")),
        ("power", ("power", "tcc", "vrm")),
        ("lighting/input", ("backlight", "rgb", "lightbar", "command_trigger")),
        ("platform", ("bios", "support", "project", "system_id", "ap_oem")),
    ]
    for name, words in groups:
        if any(word in joined for word in words):
            result.append(name)
    return result or ["other"]


def analyze(text):
    functions = []
    for match in FUNCTION.finditer(text):
        body = match.group(3)
        symbols = sorted(set(EC_SYMBOL.findall(body)))
        if symbols:
            functions.append({
                "address": int(match.group(1), 16),
                "name": match.group(2),
                "symbols": symbols,
                "domains": domains(symbols),
                "lines": len(body.splitlines()),
            })
    return functions


def render(source, functions):
    lines = [
        "# Semantic EC function index", "", f"Source: `{source}`", "",
        "Only functions with at least one named EC/XRAM reference are included.", "",
        "| CODE | Function | Domain | Named EC/XRAM references | Lines |",
        "|---:|---|---|---|---:|",
    ]
    for item in functions:
        symbols = ", ".join(f"`{name}`" for name in item["symbols"])
        lines.append(f"| `0x{item['address']:04X}` | `{item['name']}` | "
                     f"{', '.join(item['domains'])} | {symbols} | {item['lines']} |")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="pseudo-C produced by ExportReadableEc")
    parser.add_argument("-o", "--output", required=True)
    args = parser.parse_args(argv)
    source = pathlib.Path(args.input)
    functions = analyze(source.read_text(errors="replace"))
    pathlib.Path(args.output).write_text(render(source, functions))
    print(f"indexed {len(functions)} functions with named EC references")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
