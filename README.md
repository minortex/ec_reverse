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

### 3. Build the flasher

```sh
make -C tools/ec-flash
```

> **Warning:** every `ec-flash` invocation enters Follow Mode: the fans spin up
> to full speed, the built-in keyboard is locked, and the EC watchdog cuts power
> after about three minutes. Use an external USB keyboard, and run **one**
> command per session — do not queue up several reads and then the write.

### 4. Write

```sh
sudo build/ec-flash --write flexicharge-v2.2.bin
```

Erases, programs and verifies in one pass. Don't cut power. A successful write
arms an EC reset for the next shutdown.

The write path is reconstructed from the vendor `ifux64.efi` and untested on
hardware — keep an external SPI programmer ready.

Do any `--dump` / `--id` in a separate session beforehand.

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
| `tools/ec/i2ec_rw.py` | raw EC byte read/write over ports `0x681–0x683`; reaches the full 16-bit XRAM, including `0x0800–0x0BFF` that the 4 KiB H2RAM window cannot see |
| `tools/analysis/` | offline image analysis, disassembly annotation, Ghidra export |
| `docs/00-overview.md` | document map, confidence levels, open questions |
| `docs/02-flash-protocol.md` | Follow Mode / SPI protocol and tool constraints |
| `docs/05-calibration-and-limits.md` | calibration state machine, `0x07B9`/`0x07D0` |
| `docs/07-mods.md` | patch catalogue, byte ranges, validation gates |

What FlexiCharge v2.2 actually changes — hook offsets, cave layout, the `0x0773`
persistence slot, the `0xA5/0x78` commit handshake and rollback limits — is written
up in [`docs/ec-firmware-flexicharge-v2.2-20260729.md`](docs/ec-firmware-flexicharge-v2.2-20260729.md)
and summarised in [`docs/05-calibration-and-limits.md`](docs/05-calibration-and-limits.md).
