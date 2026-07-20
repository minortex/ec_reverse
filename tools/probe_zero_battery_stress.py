#!/usr/bin/env python3
"""Zero battery stress for a probe, optionally restoring the original value."""

from __future__ import annotations

import argparse
import time

from i2ec_rw import DEFAULT_BASE_PORT, format_byte, open_i2ec


CONFIRMATION = "I_UNDERSTAND_TEMPORARY_STRESS_WRITE"
KEEP_CONFIRMATION = "I_UNDERSTAND_ZERO_STRESS_WILL_PERSIST"
STRESS_ADDRESSES = (0x09C7, 0x09C8, 0x09C9, 0x09CA)


def parse_integer(value: str) -> int:
    return int(value, 0)


def read_bytes(i2ec, addresses: tuple[int, ...]) -> tuple[int, ...]:
    return tuple(i2ec.read(address) for address in addresses)


def stress_word(values: tuple[int, ...]) -> int:
    return (values[2] << 8) | values[3]


def read_target(i2ec) -> tuple[int, int, int, int]:
    desired = i2ec.read(0x0522) | (i2ec.read(0x0523) << 8)
    queued = i2ec.read(0x0836) | (i2ec.read(0x0837) << 8)
    return desired, queued, i2ec.read(0x0490), i2ec.read(0x0832)


def write_and_verify(i2ec, values: tuple[int, ...]) -> None:
    for address, value in zip(STRESS_ADDRESSES, values):
        i2ec.write(address, value)
    actual = read_bytes(i2ec, STRESS_ADDRESSES)
    if actual != values:
        expected_text = " ".join(f"{value:02X}" for value in values)
        actual_text = " ".join(f"{value:02X}" for value in actual)
        raise RuntimeError(
            f"stress verification failed: expected {expected_text}, got {actual_text}"
        )


def observe(i2ec, seconds: float, label: str) -> None:
    deadline = time.monotonic() + seconds
    previous = None
    while time.monotonic() < deadline:
        current = read_target(i2ec)
        if current != previous:
            desired, queued, power, pending = current
            print(
                f"{label} target={desired}mV queued={queued}mV "
                f"0490=0x{power:02X} pending=0x{pending:02X}",
                flush=True,
            )
            previous = current
        time.sleep(0.05)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Temporarily zero XRAM[0x09C7:0x09CA], observe the desired/queued "
            "charge target, and restore the original four bytes."
        )
    )
    parser.add_argument("--expect-stress", type=parse_integer, required=True,
                        help="required pre-write stress word, for example 0x2BDF")
    parser.add_argument("--hold-seconds", type=float, default=5.0,
                        help="zero-stress observation time, default 5 seconds")
    parser.add_argument("--restore-seconds", type=float, default=3.0,
                        help="post-restore observation time, default 3 seconds")
    parser.add_argument("--base-port", type=parse_integer, default=DEFAULT_BASE_PORT)
    parser.add_argument("--device", default="/dev/port")
    parser.add_argument("--allow-write", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--keep-zero", action="store_true",
                        help="do not restore the original stress bytes")
    parser.add_argument("--confirm-keep")
    args = parser.parse_args()

    if not 0 <= args.expect_stress <= 0xFFFF:
        raise ValueError("--expect-stress must be within 0x0000-0xFFFF")
    if args.hold_seconds < 1 or args.restore_seconds < 1:
        raise ValueError("observation times must be at least one second")
    if not args.allow_write or args.confirm != CONFIRMATION:
        raise RuntimeError(
            "temporary write is locked; pass --allow-write and "
            f"--confirm {CONFIRMATION} only for the controlled diagnostic probe"
        )
    if args.keep_zero and args.confirm_keep != KEEP_CONFIRMATION:
        raise RuntimeError(
            "persistent zero is locked; pass "
            f"--confirm-keep {KEEP_CONFIRMATION} only when accepting that the EC may "
            "raise its desired charge target"
        )

    with open_i2ec(args.base_port, args.device) as i2ec:
        control = i2ec.read(0x200D)
        if control & 0x03 != 0x03:
            raise RuntimeError(
                "EC does not report I2EC read-write mode "
                f"(SPCTRL1={format_byte(control)})"
            )

        original = read_bytes(i2ec, STRESS_ADDRESSES)
        original_stress = stress_word(original)
        if original_stress != args.expect_stress:
            raise RuntimeError(
                f"stress changed: expected 0x{args.expect_stress:04X}, "
                f"found 0x{original_stress:04X}; no write was performed"
            )

        print(
            "saved EC[09C7:09CA]=" + " ".join(f"{value:02X}" for value in original),
            flush=True,
        )
        observe(i2ec, 0.1, "BEFORE")
        zero_written = False
        try:
            write_and_verify(i2ec, (0, 0, 0, 0))
            zero_written = True
            print("ZEROED EC[09C7:09CA]=00 00 00 00", flush=True)
            observe(i2ec, args.hold_seconds, "ZERO")
        finally:
            if zero_written and not args.keep_zero:
                write_and_verify(i2ec, original)
                print(
                    "RESTORED EC[09C7:09CA]="
                    + " ".join(f"{value:02X}" for value in original),
                    flush=True,
                )
                observe(i2ec, args.restore_seconds, "RESTORED")
            elif zero_written:
                final = read_bytes(i2ec, STRESS_ADDRESSES)
                print(
                    "KEPT EC[09C7:09CA]="
                    + " ".join(f"{value:02X}" for value in final)
                    + " (09C7/09C8 may resume counting; stress word remains zero)",
                    flush=True,
                )


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError) as error:
        raise SystemExit(f"error: {error}")
