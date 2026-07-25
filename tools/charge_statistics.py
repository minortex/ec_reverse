#!/usr/bin/env python3
"""Continuously display EC charge statistics with 16-bit fields assembled."""

from __future__ import annotations

import argparse
import signal
import sys
import time

from i2ec_rw import DEFAULT_BASE_PORT, open_i2ec


RSOC_ADDRESS = 0x04AB
PACK_VOLTAGE_ADDRESS = 0x0438
BATTERY_CURRENT_ADDRESS = 0x050A
ACCUMULATED_STATE_ADDRESS = 0x0388


def read_le_word(i2ec, address: int) -> int:
    """Read a word whose low byte is stored at the lower address."""
    low = i2ec.read(address)
    high = i2ec.read(address + 1)
    return low | (high << 8)


def read_be_word(i2ec, address: int) -> int:
    """Read a word whose high byte is stored at the lower address."""
    high = i2ec.read(address)
    low = i2ec.read(address + 1)
    return (high << 8) | low


def read_le_signed_word(i2ec, address: int) -> int:
    """Read a little-endian signed 16-bit word."""
    value = read_le_word(i2ec, address)
    return value - 0x10000 if value & 0x8000 else value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="持续显示 EC 电量百分比、电压和当前累计状态"
    )
    parser.add_argument(
        "-i",
        "--interval",
        type=float,
        default=0.5,
        help="采样间隔秒数（默认：0.5）",
    )
    parser.add_argument(
        "--base-port",
        type=lambda value: int(value, 0),
        default=DEFAULT_BASE_PORT,
        help="I2EC 基址（默认：0x680）",
    )
    parser.add_argument("--device", default="/dev/port", help="端口设备")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error("--interval 必须大于 0")

    stop = False

    def request_stop(signum, frame):
        nonlocal stop
        stop = True

    old_sigint = signal.signal(signal.SIGINT, request_stop)
    old_sigterm = signal.signal(signal.SIGTERM, request_stop)
    try:
        with open_i2ec(args.base_port, args.device) as i2ec:
            while not stop:
                rsoc = i2ec.read(RSOC_ADDRESS)
                # SBS telemetry at 0x0438 is little-endian.
                pack_voltage = read_le_word(i2ec, PACK_VOLTAGE_ADDRESS)
                # Battery current at 0x050A is signed little-endian mA.
                battery_current = read_le_signed_word(i2ec, BATTERY_CURRENT_ADDRESS)
                # The internal work-area word at 0x0388 is big-endian.
                accumulated_state = read_be_word(i2ec, ACCUMULATED_STATE_ADDRESS)

                print(f"百分比: {rsoc}%")
                print(f"电压: {pack_voltage} mV")
                print(f"电流: {battery_current} mA")
                power_watts = pack_voltage * battery_current / 1_000_000
                print(f"功率: {power_watts:+.3f} W")
                print(f"当前累计状态: {accumulated_state} (0x{accumulated_state:04X})")
                print("-------------", flush=True)

                if not stop:
                    time.sleep(args.interval)
    finally:
        signal.signal(signal.SIGINT, old_sigint)
        signal.signal(signal.SIGTERM, old_sigterm)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
