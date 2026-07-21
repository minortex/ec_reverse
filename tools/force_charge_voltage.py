#!/usr/bin/env python3
"""Temporarily force the EC charger target while AC remains connected."""

from __future__ import annotations

import argparse
import time

from i2ec_rw import DEFAULT_BASE_PORT, open_i2ec


TARGET_LO = 0x0522
TARGET_HI = 0x0523
QUEUED_LO = 0x0836
QUEUED_HI = 0x0837
PENDING = 0x0832


def read_le(i2ec, low: int, high: int) -> int:
    return i2ec.read(low) | (i2ec.read(high) << 8)


def read_base(i2ec) -> int:
    return (i2ec.read(0x030E) << 8) | i2ec.read(0x030F)


def write_target(i2ec, millivolts: int) -> None:
    low = millivolts & 0xFF
    high = millivolts >> 8
    # Low byte first makes the intermediate value lower, never higher.
    i2ec.write(TARGET_LO, low)
    i2ec.write(TARGET_HI, high)
    i2ec.write(QUEUED_LO, low)
    i2ec.write(QUEUED_HI, high)
    i2ec.write(PENDING, i2ec.read(PENDING) | 0x02)


def check_safety(i2ec, requested: int) -> tuple[int, int, int]:
    base = read_base(i2ec)
    power = i2ec.read(0x0490)
    session = i2ec.read(0x0497)
    cell_code = (i2ec.read(0x0491) >> 6) & 0x03
    temp_raw = read_le(i2ec, 0x04A2, 0x04A3)
    pack = read_le(i2ec, 0x0438, 0x0439)

    if requested != base:
        raise RuntimeError(
            f"requested {requested}mV differs from battery-provided base {base}mV"
        )
    if power & 0x07 != 0x07 or session & 0x01 == 0:
        raise RuntimeError(
            f"AC/battery gates are not valid (0490=0x{power:02X}, 0497=0x{session:02X})"
        )
    if cell_code != 0x03:
        raise RuntimeError(f"battery is not reported as 4S (0491 cell code={cell_code})")
    if temp_raw >= 3182:  # Approximately 45.0 C in 0.1 K units.
        raise RuntimeError(f"battery temperature is too high (raw={temp_raw}, limit=3182)")
    if pack >= requested:
        raise RuntimeError(f"pack voltage {pack}mV is already at/above {requested}mV")
    return base, pack, temp_raw


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--millivolts", type=int, default=17600)
    parser.add_argument("--hold-seconds", type=float, default=108000.0)
    parser.add_argument("--interval", type=float, default=0.02)
    parser.add_argument("--base-port", type=lambda value: int(value, 0), default=DEFAULT_BASE_PORT)
    parser.add_argument("--device", default="/dev/port")
    args = parser.parse_args()

    if not 1 <= args.hold_seconds <= 108000:
        raise ValueError("--hold-seconds must be between 1 and 108000")
    if not 0.01 <= args.interval <= 1:
        raise ValueError("--interval must be between 0.01 and 1 second")

    with open_i2ec(args.base_port, args.device) as i2ec:
        control = i2ec.read(0x200D)
        if control & 0x03 != 0x03:
            raise RuntimeError(f"I2EC is not read-write (SPCTRL1=0x{control:02X})")

        base, pack, temp_raw = check_safety(i2ec, args.millivolts)
        print(
            f"forcing {args.millivolts}mV for {args.hold_seconds:g}s "
            f"(base={base}mV pack={pack}mV temp={(temp_raw - 2731.5) / 10:.2f}C)",
            flush=True,
        )
        deadline = time.monotonic() + args.hold_seconds
        corrections = 0
        next_safety_check = 0.0
        next_progress = 0.0
        pack = 0
        temp_raw = 0
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_safety_check:
                _, pack, temp_raw = check_safety(i2ec, args.millivolts)
                next_safety_check = now + 1.0

            desired = read_le(i2ec, TARGET_LO, TARGET_HI)
            queued = read_le(i2ec, QUEUED_LO, QUEUED_HI)
            if desired != args.millivolts or queued != args.millivolts:
                write_target(i2ec, args.millivolts)
                corrections += 1
                print(
                    f"corrected desired={desired}mV queued={queued}mV -> "
                    f"{args.millivolts}mV",
                    flush=True,
                )
            if now >= next_progress:
                rsoc = i2ec.read(0x04AB)
                current_desired = read_le(i2ec, TARGET_LO, TARGET_HI)
                current_queued = read_le(i2ec, QUEUED_LO, QUEUED_HI)
                print(
                    f"status desired={current_desired}mV queued={current_queued}mV "
                    f"pack={pack}mV rsoc={rsoc}% "
                    f"temp={(temp_raw - 2731.5) / 10:.2f}C corrections={corrections}",
                    flush=True,
                )
                next_progress = now + 60.0
            time.sleep(args.interval)

        desired = read_le(i2ec, TARGET_LO, TARGET_HI)
        queued = read_le(i2ec, QUEUED_LO, QUEUED_HI)
        print(
            f"finished corrections={corrections} desired={desired}mV queued={queued}mV; "
            "firmware control resumes now",
            flush=True,
        )


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError) as error:
        raise SystemExit(f"error: {error}")
