#!/usr/bin/env python3
"""Clear the EC battery aging stress accumulator to restore full charge voltage.

Writes zero to XRAM[0x09C7:0x09CA], the four-byte stress counter that the
aging function at CODE:D1D1 uses to derate charge voltage in 50 mV/cell tiers.

Zeroing it removes the voltage cap immediately.  The EC will resume counting
from zero, so re-run periodically or after thermal stress events to keep the
voltage unlocked.

Usage:
  sudo python3 clear_battery_stress.py
  sudo python3 clear_battery_stress.py --dry-run   # read-only preview
"""

from __future__ import annotations

import argparse
import sys
from i2ec_rw import DEFAULT_BASE_PORT, format_byte, open_i2ec

STRESS_REGS = (0x09C7, 0x09C8, 0x09C9, 0x09CA)

TIER_THRESHOLDS = [
    (0x2D00, 200, "severe  — 200 mV/cell derating"),
    (0x21C0, 150, "high    — 150 mV/cell derating"),
    (0x1950, 100, "moderate — 100 mV/cell derating"),
    (0x10E0,  50, "mild    — 50 mV/cell derating"),
]


def classify_stress(word: int) -> str:
    for threshold, mv, desc in TIER_THRESHOLDS:
        if word > threshold:
            return f"0x{word:04X} ({word}d) -> {desc}  [4S: -{4*mv}mV]"
    return f"0x{word:04X} ({word}d) -> no derating"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="read and display stress state without writing")
    parser.add_argument("--base-port", type=lambda x: int(x, 0),
                        default=DEFAULT_BASE_PORT)
    parser.add_argument("--device", default="/dev/port")
    args = parser.parse_args()

    with open_i2ec(args.base_port, args.device) as i2ec:
        ctrl = i2ec.read(0x200D)
        if ctrl & 0x03 != 0x03:
            print(f"error: I2EC not read-write (SPCTRL1=0x{ctrl:02X})", file=sys.stderr)
            sys.exit(1)

        regs = [i2ec.read(a) for a in STRESS_REGS]
        counters = regs[:2]
        stress_word = (regs[2] << 8) | regs[3]

        target = i2ec.read(0x0522) | (i2ec.read(0x0523) << 8)
        queued = i2ec.read(0x0836) | (i2ec.read(0x0837) << 8)
        base_lo = i2ec.read(0x030E)
        base_hi = i2ec.read(0x030F)
        base_v = (base_lo << 8) | base_hi

        gate = i2ec.read(0x0490)
        cells_raw = i2ec.read(0x0491)
        cell_code = (cells_raw >> 6) & 0x03
        cells = {0b11: 4, 0b10: 3}.get(cell_code, f"?({cell_code:02b})")

        print(f"Stress registers:  "
              f"09C7={format_byte(regs[0])}  09C8={format_byte(regs[1])}  "
              f"09C9={format_byte(regs[2])}  09CA={format_byte(regs[3])}")
        print(f"  Counters:  09C7={counters[0]}/60  09C8={counters[1]}/60")
        print(f"  Stress word: {classify_stress(stress_word)}")
        print(f"Gate byte 0x0490: 0x{gate:02X}  (bit1 stress_en={(gate>>1)&1})")
        print(f"Cell config 0x0491: 0x{cells_raw:02X} -> {cells}S")
        print(f"Base voltage: {base_v}mV")
        print(f"Charge target:  desired={target}mV  queued={queued}mV")

        if args.dry_run:
            print("\n[dry-run] No writes performed.")
            return

        if stress_word == 0 and all(c == 0 for c in regs):
            print("\nStress already zero - nothing to do.")
            return

        for addr in STRESS_REGS:
            i2ec.write(addr, 0x00)

        verify = [i2ec.read(a) for a in STRESS_REGS]
        if verify != [0, 0, 0, 0]:
            actual = " ".join(f"{v:02X}" for v in verify)
            print(f"\nerror: verify failed, got {actual}", file=sys.stderr)
            sys.exit(1)

        new_target = i2ec.read(0x0522) | (i2ec.read(0x0523) << 8)
        new_queued = i2ec.read(0x0836) | (i2ec.read(0x0837) << 8)

        print(f"\nCleared EC[0x09C7:0x09CA] = 00 00 00 00")
        print(f"  Charge target:  desired={new_target}mV  queued={new_queued}mV")
        if new_target > target:
            print(f"  Voltage unlocked: {target}mV -> {new_target}mV  (+{new_target - target}mV)")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
