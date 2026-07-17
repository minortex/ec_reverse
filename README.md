# EC reverse-engineering utilities

This repository contains static-analysis notes plus two conservative tools.

## Read-only hardware utility

Build with `make -C src`. The resulting `build/ec-read` only issues Follow Mode,
JEDEC-ID, and SPI READ commands; it contains no erase/program implementation.

```sh
sudo build/ec-read --id
sudo build/ec-read --dump dumps/machine-ec.bin
sudo build/ec-read --verify samples/GXxHXxx_21.200
sudo build/ec-read --dump dumps/region.bin --address 0x20000 --length 0x20000
```

Dump and verify perform two reads by default and fail if they differ. Every I/O
wait has a timeout, SIGINT/SIGTERM trigger cleanup, and KBC is re-enabled on exit.
The default address range is the complete 256 KiB image (`0` through `0x3ffff`).

Each SPI read transaction is closed with the two direct `0x05` writes observed
in `ifux64.efi`, and begins with a direct `0x01` before the opcode selector.
Top-level recovery defaults to the official reset path: `0xfe`, `0xfc`, drain
KBC, then `0xae`. Consequently the laptop is expected to reset after a successful
hardware command. `--no-reset` exists for protocol research but is not recommended.

JEDEC-ID and data-read transactions have different endings. Data dump follows
the EFI verify path: Fast Read `0x0b`, three address bytes, one dummy byte,
continuous reads up to a 64 KiB boundary, then the EFI status handshake.
Use `--single-read` only when explicitly accepting weaker consistency checking.

## Offline firmware tool

The analyzer requires only Python 3's standard library.

```sh
python3 tools/firmware_tool.py analyze samples/GXxHXxx_21.200 -o manifest.json
python3 tools/firmware_tool.py split samples/GXxHXxx_21.200 split-output
python3 tools/firmware_tool.py diff old.bin new.bin -o diff.json
```

`analyze` records whole-image/block SHA-256, byte sum, non-FF usage, long FF
ranges, prefixes, and printable strings. `split` writes 64 KiB blocks plus a
manifest. `diff` reports exact changed-byte counts and contiguous difference
clusters with block attribution and byte previews.

Run all offline checks with `make -C src test`. Hardware commands are never run
by the test target.

## Repository layout

- `src/`: C source and headers only.
- `build/`: compiler output; ignored by Git.
- `tools/`: offline analyzer and disassembler definitions.
- `tests/`: offline tests.
- `docs/`: maintained analysis and protocol documentation.
- `samples/`: vendor inputs and generated reverse-engineering output; retained
  in Git so the analysis remains reproducible.
- `dumps/`: machine-specific hardware reads; always ignored by Git.
