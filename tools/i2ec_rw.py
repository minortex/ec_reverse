#!/usr/bin/env python3
"""Read or write the IT557x EC memory space through dedicated I2EC ports."""

import argparse
import contextlib
import fcntl
import os
import sys


DEFAULT_BASE_PORT = 0x680
WRITE_CONFIRMATION = "I_UNDERSTAND_I2EC_WRITES"


def parse_integer(value):
    return int(value, 0)


def format_address(address):
    return f"0x{address:04X}"


def format_byte(value):
    return f"0x{value:02X}"


class DevPort:
    def __init__(self, path="/dev/port"):
        self.path = path
        self.fd = None

    def open(self):
        if self.fd is not None:
            return
        self.fd = os.open(self.path, os.O_RDWR | os.O_SYNC)
        fcntl.flock(self.fd, fcntl.LOCK_EX)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def read_byte(self, port):
        if self.fd is None:
            raise RuntimeError(f"{self.path} is not open")
        data = os.pread(self.fd, 1, port)
        if len(data) != 1:
            raise OSError(f"short read from I/O port 0x{port:04X}")
        return data[0]

    def write_byte(self, port, value):
        if self.fd is None:
            raise RuntimeError(f"{self.path} is not open")
        written = os.pwrite(self.fd, bytes((value,)), port)
        if written != 1:
            raise OSError(f"short write to I/O port 0x{port:04X}")


class I2EC:
    def __init__(self, port_io, base_port=DEFAULT_BASE_PORT):
        if not 0 <= base_port <= 0x0FFC:
            raise ValueError("I2EC base port must be between 0x0000 and 0x0FFC")
        if base_port & 0x03:
            raise ValueError("I2EC base port must be aligned to four ports")
        self.port_io = port_io
        self.address_high_port = base_port + 1
        self.address_low_port = base_port + 2
        self.data_port = base_port + 3

    @staticmethod
    def check_address(address):
        if not 0 <= address <= 0xFFFF:
            raise ValueError(f"EC address {address:#x} is outside 0x0000-0xFFFF")

    def select(self, address):
        self.check_address(address)
        self.port_io.write_byte(self.address_high_port, address >> 8)
        self.port_io.write_byte(self.address_low_port, address & 0xFF)

    def read(self, address):
        self.select(address)
        return self.port_io.read_byte(self.data_port)

    def write(self, address, value):
        self.check_address(address)
        if not 0 <= value <= 0xFF:
            raise ValueError(f"EC value {value:#x} is outside 0x00-0xFF")
        self.select(address)
        self.port_io.write_byte(self.data_port, value)


@contextlib.contextmanager
def open_i2ec(base_port, device):
    port_io = DevPort(device)
    port_io.open()
    try:
        yield I2EC(port_io, base_port)
    finally:
        port_io.close()


def command_read(args):
    with open_i2ec(args.base_port, args.device) as i2ec:
        value = i2ec.read(args.address)
    print(f"EC[{format_address(args.address)}] = {format_byte(value)} ({value})")


def command_dump(args):
    end = args.start + args.count
    if args.count <= 0 or end > 0x10000:
        raise ValueError("dump range must remain within 0x0000-0xFFFF")
    with open_i2ec(args.base_port, args.device) as i2ec:
        for address in range(args.start, end):
            value = i2ec.read(address)
            print(f"EC[{format_address(address)}] = {format_byte(value)} ({value})")


def command_write(args):
    if not args.allow_write or args.confirm != WRITE_CONFIRMATION:
        raise RuntimeError(
            "I2EC write is locked; pass --allow-write and "
            f"--confirm {WRITE_CONFIRMATION} only with RW firmware"
        )
    with open_i2ec(args.base_port, args.device) as i2ec:
        control = i2ec.read(0x200D)
        if control & 0x03 != 0x03:
            raise RuntimeError(
                "EC does not report I2EC read-write mode "
                f"(SPCTRL1={format_byte(control)})"
            )
        i2ec.write(args.address, args.value)
    print(f"EC[{format_address(args.address)}] <- {format_byte(args.value)}")


def build_parser():
    parser = argparse.ArgumentParser(
        description="Access 16-bit EC memory through IT557x dedicated I2EC ports",
        epilog=(
            "The stock firmware leaves I2EC disabled. Read commands require read-only "
            "or read-write I2EC firmware; writes additionally require read-write firmware."
        ),
    )
    parser.add_argument(
        "--base-port",
        type=parse_integer,
        default=DEFAULT_BASE_PORT,
        help="dedicated I2EC base port (default: 0x680)",
    )
    parser.add_argument(
        "--device", default="/dev/port", help="port device (default: /dev/port)"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    read_parser = subparsers.add_parser("read", help="read one EC memory byte")
    read_parser.add_argument("address", type=parse_integer)
    read_parser.set_defaults(handler=command_read)

    dump_parser = subparsers.add_parser("dump", help="read a range of EC memory bytes")
    dump_parser.add_argument("start", type=parse_integer)
    dump_parser.add_argument("count", type=parse_integer, nargs="?", default=16)
    dump_parser.set_defaults(handler=command_dump)

    write_parser = subparsers.add_parser("write", help="dangerously write one EC memory byte")
    write_parser.add_argument("address", type=parse_integer)
    write_parser.add_argument("value", type=parse_integer)
    write_parser.add_argument("--allow-write", action="store_true")
    write_parser.add_argument("--confirm")
    write_parser.set_defaults(handler=command_write)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.handler(args)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
