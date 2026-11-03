#!/usr/bin/env python3
"""Probe OEM byte-register offsets 0x00-0x1C and OEM commands 0x42/0x43/0x47.

The v12 hardware result proved that this battery responds to OEM word command
0x43 with the same 62-cycle value held in the EC cache.  v13 reused that proven,
read-only bank0:80D2 worker for a bounded Read Word scan.  Hardware results later
proved that offsets 0x00-0x1C form a consecutive OEM byte-register window rather
than standard SBS word fields.

After accepting the doorbell through common:0272, v13 uses successive real
bank-0 common:1B22 SMBus transactions as safe scheduling windows.  It lets each
stock transaction complete, saves the caller's full post-call CPU state, then
performs exactly one additional read-only command in that window.  Across 32
stock windows it probes:

    command indexes 0..28 -> OEM byte-register offsets 0x00..0x1C, probed as words
    command index 29     -> OEM command 0x43 (known-good control, value cached)
    command indexes 30/31-> OEM commands 0x42/0x47 (response status only)

Every command uses R3=0xA1, R5=0x0C and R7=0x00, matching the stock OEM word
read.  The returned word is written directly into this probe's cache; no
Smart Battery write transaction is generated and no EC battery cache or
published field is modified.

Compact XRAM record at 0x0A79 (69 bytes, ending before stock reference 0x0ABE)::

    +00 doorbell: 00 idle, A5 request, 5A accepted, C3 complete
    +01 magic 0x53
    +02 state: 01 waiting for next bank-0 stock SMBus window,
               02 one probe command running, 03 complete
    +03 version 0x0D
    +04..+07 success bitmap, little-endian, bits 0..31
    +08..+67 returned words for indexes 0..29, little-endian
    +68 completed command count (0x20 on normal completion)
"""

import argparse
import hashlib
from pathlib import Path

EXPECTED_SIZE = 0x40000
EXPECTED_SHA256 = "aa4a52ab7bbeff22e38b0845bc627b735c7b7e6b28ca6868093454a5696dd5d5"

MAIN_HOOK_OFFSET = 0x00272
MAIN_HOOK_ORIGINAL = bytes.fromhex("12 15 22")
MAIN_HOOK_CONTEXT_OFFSET = 0x0026F
MAIN_HOOK_CONTEXT = bytes.fromhex("12 06 0a 12 15 22 ef f4 70 f9 12 15 28")
SMBUS_VENEER_OFFSET = 0x01B22
SMBUS_VENEER_ORIGINAL = bytes.fromhex("90 80 d2 02 11 00")
SMBUS_VENEER_CONTEXT_OFFSET = 0x01B1C
SMBUS_VENEER_CONTEXT = bytes.fromhex(
    "90 c5 4a 02 11 28 90 80 d2 02 11 00 90 a7 2f 02 11 00 90 a7 1f"
)
CAVE_OFFSET = 0x07E6A
CAVE_LOGICAL = 0x7E6A
CAVE_LIMIT = 0x08000

ORIGINAL_MAIN_VENEER_OFFSET = 0x01522
ORIGINAL_MAIN_VENEER = bytes.fromhex("90 90 82 02 11 28")
COMMON_BANK0_CALL_GATE_OFFSET = 0x01100
COMMON_BANK0_CALL_GATE_PREFIX = bytes.fromhex(
    "c0 08 74 11 c0 e0 c0 82 c0 83 75 08 0a c2 90 c2 91 c2 92 22"
)
TRANSACTION_WORKER_OFFSET = 0x080D2
TRANSACTION_WORKER_PREFIX = bytes.fromhex(
    "90 0a 6c ed f0 a3 eb f0 90 0a 6b ef f0 64 01 70 0a"
)
OEM43_STOCK_OFFSET = 0x14462
OEM43_STOCK_CONTEXT = bytes.fromhex(
    "90 0a 6e 74 43 12 e8 a7 7d 0c ff 12 1b 22 ef 60 2c "
    "90 00 c1 e0 fe 90 00 c0 e0 90 03 43 12 e8 c4"
)
OEM42_STOCK_OFFSET = 0x14512
OEM42_STOCK_CONTEXT = bytes.fromhex(
    "90 0a 6e 74 42 12 e8 a7 12 c5 de 70 03 02 c5 d5 "
    "90 00 c0 e0 90 03 42 f0"
)
OEM47_STOCK_OFFSET = 0x1452A
OEM47_STOCK_CONTEXT = bytes.fromhex(
    "90 0a 6e 74 47 12 e8 a7 12 c5 de 70 03 02 c5 d5"
)
OEM_WORD_WRAPPER_OFFSET = 0x145DE
OEM_WORD_WRAPPER = bytes.fromhex("7d 0c ff 12 1b 22 ef 22")
E8A7_OFFSET = 0x168A7
E8A7_HELPER = bytes.fromhex(
    "f0 7e 00 a3 74 00 f0 a3 74 c0 f0 e4 a3 f0 7b a1 22"
)

DOORBELL = 0x0A79
CACHE_MAGIC = 0x0A7A
CACHE_STATE = 0x0A7B
CACHE_VERSION = 0x0A7C
CACHE_MASK = 0x0A7D
CACHE_DATA = 0x0A81
CACHE_COUNT = 0x0ABD
CACHE_END = CACHE_COUNT
COMMAND_COUNT = 32
BYTE_WINDOW_COMMAND_COUNT = 29
OEM43_INDEX = 29
OEM42_INDEX = 30
OEM47_INDEX = 31


class Code:
    def __init__(self, origin: int):
        self.origin = origin
        self.data = bytearray()
        self.labels = {}
        self.rel8_fixups = []
        self.abs16_fixups = []

    @property
    def pc(self):
        return self.origin + len(self.data)

    def emit(self, *values):
        self.data.extend(values)

    def label(self, name):
        if name in self.labels:
            raise ValueError(f"duplicate label: {name}")
        self.labels[name] = self.pc

    def rel8(self, opcode, target):
        self.emit(opcode, 0)
        self.rel8_fixups.append((len(self.data) - 1, target))

    def abs_jump(self, opcode, target):
        self.emit(opcode, 0, 0)
        self.abs16_fixups.append((len(self.data) - 2, target))

    def lcall(self, target):
        self.abs_jump(0x12, target)

    def finish(self):
        for operand_index, target_name in self.rel8_fixups:
            target = self.labels[target_name]
            next_pc = self.origin + operand_index + 1
            displacement = target - next_pc
            if not -128 <= displacement <= 127:
                raise ValueError(f"relative target {target_name} out of range: {displacement}")
            self.data[operand_index] = displacement & 0xFF
        for operand_index, target_name in self.abs16_fixups:
            target = self.labels[target_name]
            self.data[operand_index] = target >> 8
            self.data[operand_index + 1] = target & 0xFF
        return bytes(self.data)


def mov_dptr(code, address):
    code.emit(0x90, address >> 8, address & 0xFF)


def build_cave():
    code = Code(CAVE_LOGICAL)

    code.label("main_entry")
    mov_dptr(code, DOORBELL)
    code.emit(0xE0, 0x64, 0xA5)
    code.rel8(0x60, "main_accept")
    code.emit(0x02, 0x15, 0x22)

    code.label("main_accept")
    code.emit(0xC0, 0x06)
    mov_dptr(code, DOORBELL)
    code.emit(
        0x74, 0x5A, 0xF0, 0xA3,
        0x74, 0x53, 0xF0, 0xA3,
        0x74, 0x01, 0xF0, 0xA3,
        0x74, 0x0D, 0xF0,
        0xE4, 0x7E, CACHE_END - CACHE_VERSION,
    )
    code.label("clear_cache")
    code.emit(0xA3, 0xF0)
    code.rel8(0xDE, "clear_cache")
    code.emit(0xD0, 0x06, 0x02, 0x15, 0x22)

    code.label("smbus_entry")
    code.emit(0xC0, 0xE0, 0xC0, 0x82, 0xC0, 0x83)
    mov_dptr(code, DOORBELL)
    code.emit(0xE0, 0x64, 0x5A)
    code.rel8(0x60, "pending")
    code.label("bypass")
    code.emit(0xD0, 0x83, 0xD0, 0x82, 0xD0, 0xE0)
    code.abs_jump(0x02, "original_smbus_tail")

    code.label("pending")
    mov_dptr(code, CACHE_STATE)
    code.emit(0xE0, 0x64, 0x02)
    code.rel8(0x60, "bypass")
    # Only piggyback a bank-0 call so the active R0-R7 save also protects all
    # bank-0 registers used by the injected scan.
    code.emit(0xE5, 0xD0, 0x54, 0x18)
    code.rel8(0x70, "bypass")
    mov_dptr(code, CACHE_STATE)
    code.emit(0x74, 0x02, 0xF0)
    code.emit(0xD0, 0x83, 0xD0, 0x82, 0xD0, 0xE0)
    code.lcall("original_smbus_call")

    # Preserve the triggering stock call's exact post-call state.
    code.emit(0xC0, 0xE0, 0xC0, 0xF0, 0xC0, 0x82, 0xC0, 0x83)
    for register in range(8):
        code.emit(0xE8 + register, 0xC0, 0xE0)
    code.emit(0xC0, 0xD0, 0x53, 0xD0, 0xE7)

    # Preserve the stock result scratch word.  OEM 0x42/0x47 use 0x00C0 as
    # their temporary destination, but the triggering stock caller may still
    # consume its own 0x00C0/0x00C1 result after common:1B22 returns.
    mov_dptr(code, 0x00C0)
    code.emit(0xE0, 0xC0, 0xE0, 0xA3, 0xE0, 0xC0, 0xE0)

    # CACHE_COUNT doubles as the loop index and is zero from main_accept.
    mov_dptr(code, CACHE_COUNT)
    code.emit(0xE0, 0xFA)  # R2=index
    # Default destination caches indexes 0..29 at CACHE_DATA + 2*index.
    code.emit(0x7C, CACHE_DATA >> 8, 0xEA, 0x25, 0xE0, 0x24, CACHE_DATA & 0xFF, 0xF9)
    code.emit(0xEA, 0xB4, OEM43_INDEX, 0x00)
    code.rel8_fixups.append((len(code.data) - 1, "check_oem42"))
    code.emit(0x74, 0x43)
    code.rel8(0x80, "command_ready")
    code.label("check_oem42")
    code.emit(0xEA, 0xB4, OEM42_INDEX, 0x00)
    code.rel8_fixups.append((len(code.data) - 1, "check_oem47"))
    code.emit(0x74, 0x42)
    code.rel8(0x80, "vendor_scratch")
    code.label("check_oem47")
    code.emit(0xEA, 0xB4, OEM47_INDEX, 0x00)
    code.rel8_fixups.append((len(code.data) - 1, "standard_command"))
    code.emit(0x74, 0x47)
    code.label("vendor_scratch")
    code.emit(0x7C, 0x00, 0x79, 0xC0)
    code.rel8(0x80, "command_ready")
    code.label("standard_command")
    code.emit(0xEA)
    code.label("command_ready")

    # Descriptor: command, destination high, destination low, flags.
    mov_dptr(code, 0x0A6E)
    code.emit(0xF0, 0x7E, 0x00, 0xA3, 0xEC, 0xF0, 0xA3, 0xE9, 0xF0, 0xA3, 0xE4, 0xF0)
    code.emit(0x7B, 0xA1, 0x7D, 0x0C, 0x7F, 0x00)
    code.lcall("original_smbus_call")

    # Keep the injected transaction's Boolean return while restoring the
    # triggering stock transaction's scratch word exactly.
    code.emit(0xEF, 0xFE)
    mov_dptr(code, 0x00C1)
    code.emit(0xD0, 0xE0, 0xF0)
    mov_dptr(code, 0x00C0)
    code.emit(0xD0, 0xE0, 0xF0, 0xEE)
    code.rel8(0x60, "command_failed")

    # Set success bitmap[index // 8] bit (index % 8).
    mov_dptr(code, CACHE_COUNT)
    code.emit(0xE0, 0x75, 0xF0, 0x08, 0x84)  # A=index; B=8; DIV AB
    code.emit(0x24, CACHE_MASK & 0xFF, 0xF5, 0x82, 0xE4, 0x34, CACHE_MASK >> 8, 0xF5, 0x83)
    code.emit(0xE5, 0xF0)
    code.rel8(0x60, "bit_ready_zero")
    code.emit(0xF8, 0x74, 0x01)
    code.label("rotate_bit")
    code.emit(0x23)
    code.rel8(0xD8, "rotate_bit")
    code.rel8(0x80, "bit_ready")
    code.label("bit_ready_zero")
    code.emit(0x74, 0x01)
    code.label("bit_ready")
    code.emit(0xF9, 0xE0, 0x49, 0xF0)

    code.label("command_failed")
    mov_dptr(code, CACHE_COUNT)
    code.emit(0xE0, 0x04, 0xF0, 0xB4, COMMAND_COUNT, 0x00)
    code.rel8_fixups.append((len(code.data) - 1, "more_commands"))

    mov_dptr(code, CACHE_STATE)
    code.emit(0x74, 0x03, 0xF0)
    mov_dptr(code, DOORBELL)
    code.emit(0x74, 0xC3, 0xF0)
    code.rel8(0x80, "restore_caller")

    code.label("more_commands")
    mov_dptr(code, CACHE_STATE)
    code.emit(0x74, 0x01, 0xF0)

    code.label("restore_caller")
    code.emit(0xD0, 0xD0)
    for register in reversed(range(8)):
        code.emit(0xD0, 0xE0, 0xF8 + register)
    code.emit(0xD0, 0x83, 0xD0, 0x82, 0xD0, 0xF0, 0xD0, 0xE0, 0x22)

    code.label("original_smbus_call")
    code.emit(0x90, 0x80, 0xD2, 0x12, 0x11, 0x00, 0x22)
    code.label("original_smbus_tail")
    code.emit(0x90, 0x80, 0xD2, 0x02, 0x11, 0x00)
    return code.finish(), dict(code.labels)


CAVE, CAVE_LABELS = build_cave()
MAIN_HOOK_REPLACEMENT = bytes((0x12, CAVE_LABELS["main_entry"] >> 8, CAVE_LABELS["main_entry"] & 0xFF))
SMBUS_HOOK_REPLACEMENT = bytes((0x02, CAVE_LABELS["smbus_entry"] >> 8, CAVE_LABELS["smbus_entry"] & 0xFF))


def require_bytes(image, offset, expected, label):
    actual = image[offset : offset + len(expected)]
    if actual != expected:
        raise SystemExit(f"unexpected bytes for {label} at 0x{offset:05X}: {actual.hex(' ')}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.source.resolve() == args.output.resolve():
        raise SystemExit("refusing to overwrite source image")
    source = args.source.read_bytes()
    digest = hashlib.sha256(source).hexdigest()
    if len(source) != EXPECTED_SIZE or digest != EXPECTED_SHA256:
        raise SystemExit(f"refusing unknown image: size=0x{len(source):X}, sha256={digest}")

    for offset, expected, label in (
        (MAIN_HOOK_OFFSET, MAIN_HOOK_ORIGINAL, "common:0272 main-loop call"),
        (MAIN_HOOK_CONTEXT_OFFSET, MAIN_HOOK_CONTEXT, "common main-loop context"),
        (SMBUS_VENEER_OFFSET, SMBUS_VENEER_ORIGINAL, "common:1B22 SMBus veneer"),
        (SMBUS_VENEER_CONTEXT_OFFSET, SMBUS_VENEER_CONTEXT, "common SMBus veneer context"),
        (ORIGINAL_MAIN_VENEER_OFFSET, ORIGINAL_MAIN_VENEER, "common:1522 original veneer"),
        (COMMON_BANK0_CALL_GATE_OFFSET, COMMON_BANK0_CALL_GATE_PREFIX, "common:1100 bank0 dispatch"),
        (TRANSACTION_WORKER_OFFSET, TRANSACTION_WORKER_PREFIX, "bank0:80D2 SMBus worker"),
        (OEM43_STOCK_OFFSET, OEM43_STOCK_CONTEXT, "bank1:C462 OEM 0x43 stock path"),
        (OEM42_STOCK_OFFSET, OEM42_STOCK_CONTEXT, "bank1:C512 OEM 0x42 stock path"),
        (OEM47_STOCK_OFFSET, OEM47_STOCK_CONTEXT, "bank1:C52A OEM 0x47 stock path"),
        (OEM_WORD_WRAPPER_OFFSET, OEM_WORD_WRAPPER, "bank1:C5DE OEM word-read wrapper"),
        (E8A7_OFFSET, E8A7_HELPER, "bank1:E8A7 descriptor helper"),
    ):
        require_bytes(source, offset, expected, label)

    if CAVE_LOGICAL + len(CAVE) > CAVE_LIMIT:
        raise SystemExit(f"word-scan cave exceeds fixed common: end=0x{CAVE_LOGICAL + len(CAVE):04X}, size={len(CAVE)}")
    require_bytes(source, CAVE_OFFSET, b"\xFF" * len(CAVE), "fixed-common code cave")
    for address in range(DOORBELL, CACHE_END + 1):
        if bytes((0x90, address >> 8, address & 0xFF)) in source:
            raise SystemExit(f"candidate XRAM address 0x{address:04X} has a direct DPTR reference")

    patched = bytearray(source)
    patched[MAIN_HOOK_OFFSET : MAIN_HOOK_OFFSET + 3] = MAIN_HOOK_REPLACEMENT
    patched[SMBUS_VENEER_OFFSET : SMBUS_VENEER_OFFSET + 3] = SMBUS_HOOK_REPLACEMENT
    patched[CAVE_OFFSET : CAVE_OFFSET + len(CAVE)] = CAVE
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(patched)

    print(f"source sha256:  {digest}")
    print(f"patched sha256: {hashlib.sha256(patched).hexdigest()}")
    print(f"main hook:       common:0272 -> common:{CAVE_LABELS['main_entry']:04X}")
    print(f"smbus hook:      common:1B22 -> common:{CAVE_LABELS['smbus_entry']:04X}")
    print("probe:           read-only OEM offsets 0x00-0x1C plus OEM 0x42/0x43/0x47")
    print(f"cave:            common:{CAVE_LOGICAL:04X}-{CAVE_LOGICAL + len(CAVE) - 1:04X} ({len(CAVE)} bytes)")
    print(f"doorbell/cache:  XRAM 0x{DOORBELL:04X}-0x{CACHE_END:04X}")
    print(f"wrote:           {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
