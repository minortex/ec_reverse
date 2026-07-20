#!/usr/bin/env python3
"""Read-only live monitor for the EC battery-aging state via I2EC."""

from __future__ import annotations

import argparse
import datetime as dt
import signal
import sys
import time

from i2ec_rw import DEFAULT_BASE_PORT, open_i2ec


def read_word(i2ec, low: int, high: int) -> int:
    """Read a little-endian word; callers use explicit address order."""
    lo = i2ec.read(low)
    hi = i2ec.read(high)
    return lo | (hi << 8)


def read_sample(i2ec) -> dict[str, int]:
    base_hi = i2ec.read(0x030E)
    base_lo = i2ec.read(0x030F)
    stress_hi = i2ec.read(0x09C9)
    stress_lo = i2ec.read(0x09CA)
    return {
        "control": i2ec.read(0x200D),
        "power": i2ec.read(0x0490),
        "cells_code": i2ec.read(0x0491),
        "base": (base_hi << 8) | base_lo,
        "pack": read_word(i2ec, 0x0438, 0x0439),
        "temp_raw": read_word(i2ec, 0x04A2, 0x04A3),
        "cycles": read_word(i2ec, 0x04A6, 0x04A7),
        "target": read_word(i2ec, 0x0522, 0x0523),
        "gate_05b9": i2ec.read(0x05B9),
        "mode": i2ec.read(0x07A6),
        "aux": i2ec.read(0x0A54),
        "pending": i2ec.read(0x0832),
        "queued": read_word(i2ec, 0x0836, 0x0837),
        "sample_counter": i2ec.read(0x09C7),
        "window_counter": i2ec.read(0x09C8),
        "stress": (stress_hi << 8) | stress_lo,
        "smbus_result": i2ec.read(0x0A73),
        "smbus_status": i2ec.read(0x0A74),
        "smbus_success": i2ec.read(0x0A75),
    }


def cells_from_code(value: int) -> int:
    code = value & 0xC0
    return 4 if code == 0xC0 else 3 if code == 0x80 else 2


def stress_tier(stress: int) -> int:
    for threshold, tier in ((0x3DE0, 250), (0x2D00, 200), (0x21C0, 150),
                            (0x1950, 100), (0x10E0, 50)):
        if stress > threshold:
            return tier
    return 0


def describe(sample: dict[str, int]) -> dict[str, object]:
    cells = cells_from_code(sample["cells_code"])
    temp_c = sample["temp_raw"] / 10.0 - 273.15
    tier = stress_tier(sample["stress"])
    power = sample["power"]
    return {
        **sample,
        "cells": cells,
        "temp_c": temp_c,
        "stress_tier": tier,
        "expected_from_stress": sample["base"] - cells * tier,
        "ac": bool(power & 0x01),
        "gate0": bool(power & 0x01),
        "gate1": bool(power & 0x02),
        "gate2": bool(power & 0x04),
    }


def format_sample(sample: dict[str, object]) -> str:
    stamp = dt.datetime.now().astimezone().isoformat(timespec="milliseconds")
    power = int(sample["power"])
    gates = f"{int(bool(power & 1))}/{int(bool(power & 2))}/{int(bool(power & 4))}"
    # Keep each event compact enough for an 80-column terminal; indentation
    # makes continuation lines easy to scan as one event.
    return (
        f"{stamp}  AC={int(bool(power & 1))} 0490=0x{power:02X} gates={gates} "
        f"cells={int(sample['cells'])}"
        f"\n    pack={int(sample['pack']):5d}mV "
        f"base={int(sample['base']):5d}mV target={int(sample['target']):5d}mV "
        f"queued={int(sample['queued']):5d}mV"
        f"\n    temp={float(sample['temp_c']):6.2f}C "
        f"cycles={int(sample['cycles']):3d} mode=0x{int(sample['mode']):02X} "
        f"stress=0x{int(sample['stress']):04X} tier={int(sample['stress_tier']):3d} "
        f"counters={int(sample['sample_counter']):02d}/{int(sample['window_counter']):02d}"
        f"\n    pending=0x{int(sample['pending']):02X} "
        f"SMBus={int(sample['smbus_result']):02X}/{int(sample['smbus_status']):02X}/"
        f"{int(sample['smbus_success']):02X} aux=0x{int(sample['aux']):02X}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="只读监听 EC 电池老化与充电电压状态")
    parser.add_argument("-i", "--interval", type=float, default=0.2,
                        help="采样间隔秒数，默认 0.2")
    parser.add_argument("--base-port", type=lambda value: int(value, 0),
                        default=DEFAULT_BASE_PORT)
    parser.add_argument("--device", default="/dev/port")
    parser.add_argument("--transients", action="store_true",
                        help="同时由共享 scratch/SMBus 中间状态触发输出")
    args = parser.parse_args()

    previous = None
    stop_requested = False

    def request_stop(signum, frame):
        nonlocal stop_requested
        stop_requested = True

    old_sigint = signal.signal(signal.SIGINT, request_stop)
    try:
        with open_i2ec(args.base_port, args.device) as i2ec:
            print("I2EC 只读监控已启动；不要运行任何 I2EC write 或其他直接 outb 工具。Ctrl-C 停止。",
                  flush=True)
            while not stop_requested:
                try:
                    current = describe(read_sample(i2ec))
                    if previous is None:
                        print("INITIAL " + format_sample(current), flush=True)
                    else:
                        ignored = {"temp_c", "expected_from_stress"}
                        if not args.transients:
                            ignored.update({"aux", "smbus_result", "smbus_status",
                                            "smbus_success"})
                        changed = [key for key in current if current[key] != previous[key]
                                   and key not in ignored]
                        if changed:
                            print("CHANGE[" + ",".join(changed) + "] " +
                                  format_sample(current), flush=True)
                    previous = current
                except (OSError, RuntimeError, ValueError) as exc:
                    print(f"READ-ERROR {exc}", file=sys.stderr, flush=True)
                if not stop_requested:
                    time.sleep(max(0.05, args.interval))
    except KeyboardInterrupt:
        # Keep compatibility with callers that install their own SIGINT handler.
        stop_requested = True
    finally:
        signal.signal(signal.SIGINT, old_sigint)
    print("\n监控结束。", flush=True)


if __name__ == "__main__":
    main()
