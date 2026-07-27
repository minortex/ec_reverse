#!/usr/bin/env python3
"""Request and/or read the experimental SBS identity cache through I2EC."""

import argparse
import sys
import time

from i2ec_rw import (
    DEFAULT_BASE_PORT,
    WRITE_CONFIRMATION,
    format_byte,
    open_i2ec,
    parse_integer,
)


CACHE_START = 0x0A79
V3_CACHE_SIZE = 10
V4_CACHE_SIZE = 25
CACHE_SIZE = V4_CACHE_SIZE
DOORBELL_ADDRESS = 0x0A79
DOORBELL_REQUEST = 0xA5
DOORBELL_ACCEPTED = 0x5A
DOORBELL_COMPLETE = 0xC3
DOORBELL_NAMES = {
    0x00: "idle/no request observed",
    DOORBELL_REQUEST: "host request pending; EC has not accepted it",
    DOORBELL_ACCEPTED: "EC accepted request; SBS reads are in progress",
    DOORBELL_COMPLETE: "EC completed the requested probe",
}
STATE_NAMES = {
    0x00: "record initialization was interrupted or is still in progress",
    0x02: "SBS reads are in progress",
    0x03: "SBS reads completed",
}


def decode_date(value):
    day = value & 0x1F
    month = (value >> 5) & 0x0F
    year = 1980 + ((value >> 9) & 0x7F)
    if 1 <= month <= 12 and 1 <= day <= 31:
        return f"{year:04d}-{month:02d}-{day:02d}"
    return "invalid packed SBS date"


def word_le(data, offset):
    return data[offset] | (data[offset + 1] << 8)


def read_snapshot(i2ec):
    return bytes(i2ec.read(CACHE_START + index) for index in range(CACHE_SIZE))


def print_record(data):
    doorbell, magic, state, mask = data[:4]
    print("raw:              " + " ".join(f"{value:02X}" for value in data))
    print(
        f"doorbell:         0x{doorbell:02X} "
        f"({DOORBELL_NAMES.get(doorbell, 'unknown')})"
    )
    print(f"magic:            0x{magic:02X}" + (" ('S')" if magic == 0x53 else ""))
    print(f"state:            0x{state:02X} ({STATE_NAMES.get(state, 'unknown')})")
    print(f"success mask:     0x{mask:02X}")

    if doorbell == DOORBELL_REQUEST:
        print(
            "result:           host write is visible, but EC has not executed the doorbell cave",
            file=sys.stderr,
        )
        return 3
    if magic != 0x53:
        print("result:           v3 doorbell cache is not present", file=sys.stderr)
        return 2

    fields = (
        (0x01, "SpecificationInfo", word_le(data, 4)),
        (0x02, "ManufactureDate", word_le(data, 6)),
        (0x04, "SerialNumber", word_le(data, 8)),
    )
    for bit, name, value in fields:
        if not mask & bit:
            print(f"{name + ':':18} read failed/not attempted")
        elif name == "ManufactureDate":
            print(f"{name + ':':18} 0x{value:04X} ({decode_date(value)})")
        elif name == "SerialNumber":
            print(f"{name + ':':18} 0x{value:04X} ({value})")
        else:
            print(f"{name + ':':18} 0x{value:04X}")

    if len(data) >= V4_CACHE_SIZE and data[10] == 0x04:
        print("protocol version: 0x04 (stock battery scheduler context)")
        print(f"session flags:    0x{data[11]:02X} (XRAM 0x0497)")
        print(f"power flags:      0x{data[12]:02X} (XRAM 0x0490)")
        diagnostics = (
            ("0x1A", data[13:17]),
            ("0x1B", data[17:21]),
            ("0x1C", data[21:25]),
        )
        for command, values in diagnostics:
            returned, result, host_status, completion = values
            if returned == 1 and result == 0 and completion == 1:
                diagnosis = "success"
            elif host_status & 0x7C:
                diagnosis = "SMBus host-status error bits set"
            elif result == 0xEE and completion == 0 and not (host_status & 0x7E):
                diagnosis = "timeout/no completion status observed"
            else:
                diagnosis = "transaction failed; inspect raw status"
            print(
                f"{command} transaction: return=0x{returned:02X} "
                f"result=0x{result:02X} host=0x{host_status:02X} "
                f"completion=0x{completion:02X} ({diagnosis})"
            )

    if doorbell == DOORBELL_COMPLETE and state == 0x03:
        print("result:           EC executed and completed the requested probe")
        return 0
    print("result:           EC started the probe but did not complete it", file=sys.stderr)
    return 1


def validate_request_args(args):
    if not args.allow_write or args.confirm != WRITE_CONFIRMATION:
        raise RuntimeError(
            "doorbell write is locked; pass --allow-write and "
            f"--confirm {WRITE_CONFIRMATION} only after flashing the v3/v4 firmware"
        )
    if args.timeout <= 0:
        raise ValueError("--timeout must be positive")
    if args.poll_interval <= 0:
        raise ValueError("--poll-interval must be positive")


def request_probe(i2ec, args):
    validate_request_args(args)
    control = i2ec.read(0x200D)
    if control & 0x03 != 0x03:
        raise RuntimeError(
            "EC does not report I2EC read-write mode "
            f"(SPCTRL1={format_byte(control)})"
        )

    previous = i2ec.read(DOORBELL_ADDRESS)
    print(f"doorbell before:  0x{previous:02X}")
    i2ec.write(DOORBELL_ADDRESS, DOORBELL_REQUEST)
    print(f"doorbell request: 0x{DOORBELL_REQUEST:02X} written")

    deadline = time.monotonic() + args.timeout
    saw_accepted = False
    while True:
        value = i2ec.read(DOORBELL_ADDRESS)
        if value == DOORBELL_ACCEPTED and not saw_accepted:
            print(f"doorbell ack:     0x{value:02X} observed")
            saw_accepted = True
        if value == DOORBELL_COMPLETE:
            print(f"doorbell done:    0x{value:02X} observed")
            return
        if time.monotonic() >= deadline:
            print(
                f"warning: doorbell did not reach 0x{DOORBELL_COMPLETE:02X} "
                f"within {args.timeout:g} seconds (last=0x{value:02X})",
                file=sys.stderr,
            )
            return
        time.sleep(args.poll_interval)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-port",
        type=parse_integer,
        default=DEFAULT_BASE_PORT,
        help="dedicated I2EC base port (default: 0x680)",
    )
    parser.add_argument("--device", default="/dev/port")
    parser.add_argument(
        "--request",
        action="store_true",
        help="write doorbell 0xA5, wait for EC completion, then read the cache",
    )
    parser.add_argument("--allow-write", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument(
        "--timeout", type=float, default=10.0, help="request timeout in seconds (default: 10)"
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=0.02,
        help="doorbell polling interval in seconds (default: 0.02)",
    )
    args = parser.parse_args(argv)

    with open_i2ec(args.base_port, args.device) as i2ec:
        if args.request:
            request_probe(i2ec, args)
        first = read_snapshot(i2ec)
        second = read_snapshot(i2ec)

    if first != second:
        print("warning: cache changed between two snapshots", file=sys.stderr)
    return print_record(second)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
