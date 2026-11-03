# EC reverse-engineering toolkit

Tools and findings for the ITE IT5571 EC in the Slimbook/WUJIE 14 (`GX4HRXL`),
firmware `GXxHXxx_21.200`. Coming from `mech-forza-control`? The driver side lives
there; this repository owns the EC side — images, flashing, and the FlexiCharge
patch.

## Flash a patched EC

### 1. Get the official image

Use the vendor `GXxHXxx_21.200` image (256 KiB) from the update package. It is
not tracked here. Check it before doing anything:

```sh
sha256sum GXxHXxx_21.200
# 34c050d30772da07ef262fc7016e0677b9b1b4cdcd90cf43d93f0f15bf6a38c2
```

### 2. Patch it

The builder pins the source digest, applies four reviewed byte-level patches, and
asserts the output digest, so a drifted offset aborts instead of producing an
unreviewed image.

```sh
uv run python firmware_mods/GXxHXxx_21.200/build-from-official-20261103/patch_ec_flexicharge_v22.py \
    GXxHXxx_21.200 flexicharge-v2.2.bin
# sha256: ff6683769da655f3cafa364ddc9cfcaad84ad28fa7122191d3fefad00a7a19cf
```

Applied in order: direct calibration commit, mode-locked voltage tiers, ApExist
ignore, FlexiCharge v2.2 (lower charge threshold with hysteresis).

### 3. Build the flasher, read before writing

```sh
make -C tools/ec-flash
sudo build/ec-flash --id                 # expect 20 40 14 20 (XMC XM25QH80)
sudo build/ec-flash --dump machine.bin   # two reads, compared byte for byte
sudo build/ec-flash --verify GXxHXxx_21.200
```

`--dump` and `--verify` never write. Keep the dump: it is your recovery
reference.

### 4. Write

```sh
sudo build/ec-flash --write flexicharge-v2.2.bin
```

- Only changed 64 KiB blocks are touched: 16 × 4 KiB sector erase, erase check
  for `FF`, 256-byte page program, then a full read-back compare.
- Never cut power during erase/program. KBC is disabled while flashing; keep
  hands off the keyboard during the 3-second countdown.
- A successful write only arms a one-shot EC reset for the next shutdown
  (`/run/ec-flash-reset-on-shutdown`). It does not reset immediately.
- The write path is reconstructed from the vendor `ifux64.efi` and has not been
  exercised on this project's hardware. Have an external SPI programmer and a
  verified full dump before you try it.

Requirements: Linux with `/dev/port`, root, `iopl(3)`, Python 3 + `uv`.

## What the patch changes

`0x07B9[6:0]` stays the upper limit; FlexiCharge adds a lower limit in `0x07D0`,
a hysteresis phase bit, and a persistent slot so the setting survives a cold EC
boot. Stock firmware never reads `0x07D0`, so on an unpatched EC the lower limit
is silently ignored and charging stops only at the upper limit.

## Other entry points

| Path | Purpose |
|---|---|
| `tools/ec-flash/` | SPI flash read/verify/write (C) |
| `tools/ec/i2ec_rw.py` | raw EC byte read/write over ports `0x681–0x683` |
| `tools/analysis/` | offline image analysis, disassembly annotation, Ghidra export |
| `docs/00-overview.md` | document map, confidence levels, open questions |
| `docs/02-flash-protocol.md` | Follow Mode / SPI protocol and tool constraints |
| `docs/05-calibration-and-limits.md` | calibration state machine, `0x07B9`/`0x07D0` |
| `docs/07-mods.md` | patch catalogue, byte ranges, validation gates |
