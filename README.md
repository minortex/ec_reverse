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

The JEDEC-ID transaction is closed with the two direct `0x05` writes observed
in `ifux64.efi`, and begins with a direct `0x01` before the opcode selector.
Top-level cleanup sends `0xfc`, drains KBC, then sends `0xae`. Hardware reset
`0xfe` is never implicit; add `--reset` only when an immediate platform reset is
actually wanted. Without it, the EC may remain powered after OS shutdown and a
long power-button reset can still be required.

JEDEC-ID and data-read transactions have different endings. Data dump follows
the EFI verify path: Fast Read `0x0b`, three address bytes, one dummy byte,
continuous reads up to a 64 KiB boundary, then the EFI status handshake.
Use `--single-read` only when explicitly accepting weaker consistency checking.

Dump persistence uses a same-directory temporary file, complete write,
`fdatasync`, atomic rename, directory `fsync`, and filesystem `syncfs`. Only
after all of those succeed can an explicitly requested `--reset` be issued.

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

## Readable 8051 export

Ghidra 12's 8051 decompiler can keep code addresses separate from EC/XRAM
addresses.  The export helper installs register names recovered from the
official GCU Service, then emits pseudo-C plus a static register-reference
index:

```sh
tools/export_readable_ec.sh samples/disasm/main-bank0.bin readable-main0 \
  - tools/ec_functions-main0.tsv
```

The bundled register vocabulary is in `tools/ec_registers.tsv`. Bank0/common
SMBus symbols are kept in `tools/ec_functions-main0.tsv`; the default
`tools/ec_functions.tsv` is for bank1 because equal 16-bit code addresses name
different functions after a bank switch. Generated
pseudo-C is an analysis aid, not buildable vendor source: indirect DPTR access,
bank switching, tables, and incorrectly discovered function boundaries still
require manual review.  Analyze each 64 KiB bank separately so the 8051's
overlapping bank addresses are not conflated.

For banked or force-disassembled `.d52` files without reliable entry points,
use the conservative text annotator.  It only labels constant DPTR loads and
therefore does not confuse an equal numeric code address with an XRAM address:

```sh
python3 tools/annotate_disassembly.py samples/disasm/main-bank1.d52 \
  -o build/main-bank1.annotated.d52 --xrefs build/main-bank1-xrefs.md
```

Bank images without vectors can be seeded from the Keil C51 wrapper table in
the common bank:

```sh
python3 tools/extract_bank_entries.py samples/disasm/main-bank0.bin \
  -o build/bank-entries.json
entries=$(python3 tools/extract_bank_entries.py samples/disasm/main-bank0.bin \
  --entries bank1)
tools/export_readable_ec.sh samples/disasm/main-bank1.bin \
  build/readable-main1 "$entries"
python3 tools/summarize_pseudoc.py \
  build/readable-main1/main-bank1.bin.c \
  -o build/readable-main1/semantic-index.md
```

Short, uniquely matching functions can carry their reviewed names to another
firmware version without assuming stable addresses:

```sh
python3 tools/translate_function_symbols.py \
  samples/disasm/main-bank1.bin build/fw210/block1.bin \
  -o build/fw210/ec-functions.tsv
```

Evidence, version comparison, limitations, and the next symbol-recovery steps
are documented in [`docs/readable_firmware.md`](docs/readable_firmware.md).
The distinction between internal 64-KiB XRAM and the protected 4-KiB host
H2RAM aperture is documented in [`docs/xram_host_access.md`](docs/xram_host_access.md).

## Repository layout

- `src/`: C source and headers only.
- `build/`: compiler output; ignored by Git.
- `tools/`: offline analyzer and disassembler definitions.
- `tests/`: offline tests.
- `docs/`: maintained analysis and protocol documentation.
- `samples/`: vendor inputs and generated reverse-engineering output; retained
  in Git so the analysis remains reproducible.
- `dumps/`: machine-specific hardware reads; always ignored by Git.
- `ref/`: reference document, it5570 datasheet.

真机 JEDEC ID、1 MiB dump 布局、哈希、复位行为和 Btrfs 持久化记录见
[`docs/hardware_validation.md`](docs/hardware_validation.md)。
