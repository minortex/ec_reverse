#!/usr/bin/env python3
"""Deterministic EC firmware inspection, splitting, and binary diffing."""
import argparse
import hashlib
import json
import pathlib
import sys

BLOCK = 0x10000

def read(path):
    return pathlib.Path(path).read_bytes()

def digest(data):
    return hashlib.sha256(data).hexdigest()

def ff_runs(data, minimum=256):
    runs, start = [], None
    for i, value in enumerate(data + b"\x00"):
        if value == 0xff and start is None:
            start = i
        elif value != 0xff and start is not None:
            if i - start >= minimum:
                runs.append({"start": start, "end": i - 1, "length": i - start})
            start = None
    return runs

def strings(data, minimum=6):
    found, start = [], None
    for i, value in enumerate(data + b"\x00"):
        if 0x20 <= value <= 0x7e:
            if start is None: start = i
        elif start is not None:
            if i - start >= minimum:
                found.append({"offset": start, "value": data[start:i].decode("ascii")})
            start = None
    return found

def analyze(path, data, ff_min=256, string_min=6):
    blocks = []
    for index, start in enumerate(range(0, len(data), BLOCK)):
        part = data[start:start + BLOCK]
        non_ff = sum(value != 0xff for value in part)
        last = next((i for i in range(len(part) - 1, -1, -1) if part[i] != 0xff), None)
        blocks.append({"index": index, "offset": start, "size": len(part), "sha256": digest(part),
                       "non_ff_bytes": non_ff, "last_non_ff": None if last is None else start + last,
                       "prefix_hex": part[:16].hex()})
    vectors = []
    for offset in (0, 0x20000):
        if offset + 3 <= len(data):
            vectors.append({"offset": offset, "opcode": data[offset],
                            "is_ljmp": data[offset] == 0x02,
                            "target": (data[offset + 1] << 8) | data[offset + 2],
                            "bytes_hex": data[offset:offset + 3].hex()})
    warnings = []
    if len(data) % BLOCK:
        warnings.append(f"image size is not a multiple of 0x{BLOCK:X}")
    if len(data) != 4 * BLOCK:
        warnings.append("image is not the expected four-block 256 KiB layout")
    if vectors and not vectors[0]["is_ljmp"]:
        warnings.append("main EC offset 0 does not begin with an 8051 LJMP")
    if len(data) >= 0x20003 and not vectors[-1]["is_ljmp"]:
        warnings.append("PD offset 0x20000 does not begin with an 8051 LJMP")
    return {"format_version": 1, "file": str(path), "size": len(data), "sha256": digest(data),
            "byte_sum": sum(data), "byte_sum_hex": f"0x{sum(data):X}", "block_size": BLOCK,
            "blocks": blocks, "entry_vectors": vectors, "warnings": warnings,
            "ff_runs": ff_runs(data, ff_min),
            "ascii_strings": strings(data, string_min)}

def diff_clusters(old, new):
    limit, clusters, start = max(len(old), len(new)), [], None
    for i in range(limit + 1):
        different = i < limit and (i >= len(old) or i >= len(new) or old[i] != new[i])
        if different and start is None: start = i
        elif not different and start is not None:
            clusters.append({"start": start, "end": i - 1, "length": i - start,
                             "block": start // BLOCK, "old_hex": old[start:min(i, start+32)].hex(),
                             "new_hex": new[start:min(i, start+32)].hex()})
            start = None
    return clusters

def write_json(value, output):
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output: pathlib.Path(output).write_text(text, encoding="utf-8")
    else: sys.stdout.write(text)

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    pa = sub.add_parser("analyze"); pa.add_argument("image"); pa.add_argument("-o", "--output")
    pa.add_argument("--ff-min", type=lambda x:int(x,0), default=256)
    pa.add_argument("--string-min", type=int, default=6)
    ps = sub.add_parser("split"); ps.add_argument("image"); ps.add_argument("directory")
    pd = sub.add_parser("diff"); pd.add_argument("old"); pd.add_argument("new"); pd.add_argument("-o", "--output")
    args = parser.parse_args(argv)
    if args.command == "analyze":
        write_json(analyze(args.image, read(args.image), args.ff_min, args.string_min), args.output)
    elif args.command == "split":
        data, directory = read(args.image), pathlib.Path(args.directory); directory.mkdir(parents=True, exist_ok=True)
        manifest = analyze(args.image, data)
        for block in manifest["blocks"]:
            start = block["offset"]
            name = f"block{block['index']}.bin"; (directory / name).write_bytes(data[start:start+BLOCK]); block["file"] = name
        write_json(manifest, directory / "manifest.json")
    else:
        old, new = read(args.old), read(args.new); clusters = diff_clusters(old, new)
        result = {"format_version": 1, "old": {"file": args.old, "size": len(old), "sha256": digest(old)},
                  "new": {"file": args.new, "size": len(new), "sha256": digest(new)},
                  "changed_bytes": sum(1 for i in range(max(len(old),len(new))) if i >= len(old) or i >= len(new) or old[i] != new[i]),
                  "clusters": clusters}
        write_json(result, args.output)
    return 0

if __name__ == "__main__": raise SystemExit(main())
