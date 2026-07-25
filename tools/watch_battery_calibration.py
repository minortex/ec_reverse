#!/usr/bin/env python3
"""Read-only CSV logger for the EC battery-capacity calibration state machine."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import signal
import sys
import time

from i2ec_rw import DEFAULT_BASE_PORT, open_i2ec


def le_word(i2ec, address: int) -> int:
    """Read public SBS-style little-endian fields."""
    return i2ec.read(address) | (i2ec.read(address + 1) << 8)


def be_word(i2ec, address: int) -> int:
    """Read big-endian work-area words used by the calibration state machine."""
    return (i2ec.read(address) << 8) | i2ec.read(address + 1)


def sample(i2ec) -> dict[str, int | str]:
    now = dt.datetime.now().astimezone()
    return {
        "timestamp": now.isoformat(timespec="milliseconds"),
        "unix_ms": int(time.time() * 1000),
        "raw_03a3": i2ec.read(0x03A3),
        "raw_03a2": i2ec.read(0x03A2),
        "raw_03a1": i2ec.read(0x03A1),
        "debounce_0361": be_word(i2ec, 0x0361),
        "pack_voltage_0363": be_word(i2ec, 0x0363),
        "pack_voltage_0384": be_word(i2ec, 0x0384),
        "voltage_low_threshold": be_word(i2ec, 0x03D0),
        "voltage_high_threshold": be_word(i2ec, 0x03D2),
        "raw_0388": be_word(i2ec, 0x0388),
        "raw_038a": be_word(i2ec, 0x038A),
        "limit_high_039b": i2ec.read(0x039B),
        "limit_low_039c": i2ec.read(0x039C),
        "raw_0342": i2ec.read(0x0342),
        "raw_0340": be_word(i2ec, 0x0340),
        "raw_03c0": i2ec.read(0x03C0),
        "rsoc": i2ec.read(0x04AB),
        "rsoc_mirror": i2ec.read(0x0347),
        "rsoc_candidate_0514": i2ec.read(0x0514),
        "design_capacity": le_word(i2ec, 0x0402),
        "full_charge_capacity": le_word(i2ec, 0x0404),
        "remaining_capacity": le_word(i2ec, 0x0436),
        "pack_voltage": le_word(i2ec, 0x0438),
        "end_point_value": le_word(i2ec, 0x04AE),
        "cycles": le_word(i2ec, 0x04A6),
        "temp_raw": le_word(i2ec, 0x04A2),
        "power_flags": i2ec.read(0x0490),
        "charger_current_request": le_word(i2ec, 0x0834),
        "power_direction_mirror": i2ec.read(0x1664),
        "session_ready": i2ec.read(0x0497),
        "pending": i2ec.read(0x0832),
        "smbus_result": i2ec.read(0x0A73),
        "smbus_status": i2ec.read(0x0A74),
        "smbus_success": i2ec.read(0x0A75),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="只读记录 EC 电池容量校正状态到 CSV")
    parser.add_argument("-i", "--interval", type=float, default=1.0,
                        help="采样间隔秒数，默认 1.0；不建议低于 0.1")
    parser.add_argument("-o", "--output", default="-",
                        help="CSV 输出文件，默认 stdout")
    parser.add_argument("--base-port", type=lambda value: int(value, 0),
                        default=DEFAULT_BASE_PORT)
    parser.add_argument("--device", default="/dev/port")
    args = parser.parse_args()
    if args.interval < 0.1:
        parser.error("--interval 不能小于 0.1 秒")

    stop = False

    def request_stop(signum, frame):
        nonlocal stop
        stop = True

    old_sigint = signal.signal(signal.SIGINT, request_stop)
    old_sigterm = signal.signal(signal.SIGTERM, request_stop)
    output = sys.stdout if args.output == "-" else open(args.output, "w", newline="")
    writer = None
    try:
        with open_i2ec(args.base_port, args.device) as i2ec:
            while not stop:
                try:
                    current = sample(i2ec)
                    if writer is None:
                        writer = csv.DictWriter(output, fieldnames=list(current))
                        writer.writeheader()
                    writer.writerow(current)
                    output.flush()
                except (OSError, RuntimeError, ValueError) as exc:
                    print(f"READ-ERROR {exc}", file=sys.stderr, flush=True)
                if not stop:
                    time.sleep(args.interval)
    finally:
        signal.signal(signal.SIGINT, old_sigint)
        signal.signal(signal.SIGTERM, old_sigterm)
        if output is not sys.stdout:
            output.close()


if __name__ == "__main__":
    main()
