#!/usr/bin/env python3
"""Observe one live battery-side SMBus transaction after a host doorbell.

v10 waited on bank1:C4A7, but the machine proved that path is not a live
periodic scheduler entry.  v11 returns to two hooks already proven reachable:

* common:0272 accepts doorbell A5 in the main loop and changes it to 5A.
* common:1B22 wraps the global stock SMBus veneer.

Unlike v6, v11 does not append transactions.  It waits for an existing stock
transaction whose incoming R3 is 0xA1, records its exact live R3/R5/R7 and
XRAM[0x0A6E:0x0A71] descriptor, lets the unmodified bank0:80D2 worker execute,
then records its return/status/data and publishes C3.  This is a read-only
telemetry step intended to identify the real battery transport context before
attempting any additional identity command.

The common:1B22 control-flow target is decoded directly from the original raw
``90 80 D2 02 11 00`` bytes: MOV DPTR,#bank0:80D2; LJMP common:1100.

XRAM protocol (40 bytes at 0x0A79)::

    +00 doorbell: 00 idle, A5 request, 5A accepted/waiting, C3 complete
    +01 magic 0x53 ('S')
    +02 state: 01 waiting, 02 matching transaction executing, 03 complete
    +03 SBS identity success mask (zero; v11 sends no added transaction)
    +04..+09 reserved identity words (zero in v11)
    +10 protocol version 0x0B
    +11 XRAM 0x0497 session flags
    +12 XRAM 0x0490 power flags
    +13 match count
    +14 incoming R3
    +15 incoming R5
    +16 incoming R7
    +17..+20 pre-transaction XRAM 0x0A6E..0x0A71 descriptor
    +21 post-call R7 return
    +22..+24 post-transaction XRAM 0x0A73..0x0A75
    +25..+26 post-transaction XRAM 0x00C0..0x00C1
    +27 filter id (1 = incoming R3 equals 0xA1)
    +28..+39 reserved
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
LIVE_BATTERY_CALL_OFFSET = 0x15A02
LIVE_BATTERY_CALL_CONTEXT_OFFSET = 0x159E9
LIVE_BATTERY_CALL_CONTEXT = bytes.fromhex(
    "90 05 61 e0 75 f0 03 90 63 31 12 7d 60 e4 93 90 0a 6e f0 "
    "12 e8 a8 7d 08 ff 12 1b 22 ef 60 31"
)
E8A8_OFFSET = 0x168A8
E8A8_HELPER = bytes.fromhex(
    "7e 00 a3 74 00 f0 a3 74 c0 f0 e4 a3 f0 7b a1 22"
)

DOORBELL = 0x0A79
CACHE_MAGIC = 0x0A7A
CACHE_STATE = 0x0A7B
CACHE_MASK = 0x0A7C
CACHE_VERSION = 0x0A83
CACHE_SESSION = 0x0A84
CACHE_POWER = 0x0A85
CACHE_MATCH_COUNT = 0x0A86
CACHE_R3 = 0x0A87
CACHE_R5 = 0x0A88
CACHE_R7 = 0x0A89
CACHE_DESCRIPTOR = 0x0A8A
CACHE_RETURN_R7 = 0x0A8E
CACHE_RESULT = 0x0A8F
CACHE_HOST = 0x0A90
CACHE_COMPLETION = 0x0A91
CACHE_DATA = 0x0A92
CACHE_FILTER = 0x0A94
CACHE_END = 0x0AA0


class Code:
    def __init__(self, origin: int):
        self.origin = origin
        self.data = bytearray()
        self.labels: dict[str, int] = {}
        self.rel8_fixups: list[tuple[int, str]] = []
        self.abs16_fixups: list[tuple[int, str]] = []

    @property
    def pc(self) -> int:
        return self.origin + len(self.data)

    def emit(self, *values: int) -> None:
        self.data.extend(values)

    def label(self, name: str) -> None:
        if name in self.labels:
            raise ValueError(f"duplicate label: {name}")
        self.labels[name] = self.pc

    def rel8(self, opcode: int, target: str) -> None:
        self.emit(opcode, 0)
        self.rel8_fixups.append((len(self.data) - 1, target))

    def abs_jump(self, opcode: int, target: str) -> None:
        self.emit(opcode, 0, 0)
        self.abs16_fixups.append((len(self.data) - 2, target))

    def lcall(self, target: str) -> None:
        self.abs_jump(0x12, target)

    def finish(self) -> bytes:
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


def emit_mov_dptr(code: Code, address: int) -> None:
    code.emit(0x90, address >> 8, address & 0xFF)


def emit_copy_xram_byte(code: Code, source: int, destination: int) -> None:
    emit_mov_dptr(code, source)
    code.emit(0xE0)
    emit_mov_dptr(code, destination)
    code.emit(0xF0)


def build_cave() -> tuple[bytes, dict[str, int]]:
    code = Code(CAVE_LOGICAL)

    code.label("main_entry")
    emit_mov_dptr(code, DOORBELL)
    code.emit(0xE0, 0x64, 0xA5)
    code.rel8(0x60, "main_accept")
    code.emit(0x02, 0x15, 0x22)  # LJMP original common:1522

    code.label("main_accept")
    code.emit(0xC0, 0x06)  # preserve direct RAM 0x06 used as clear-loop counter
    emit_mov_dptr(code, DOORBELL)
    code.emit(0x74, 0x5A, 0xF0)        # accepted / waiting
    code.emit(0xA3, 0x74, 0x53, 0xF0)  # magic
    code.emit(0xA3, 0x74, 0x01, 0xF0)  # state
    code.emit(0xA3, 0xE4, 0xF0)        # mask=0 and A=0
    code.emit(0x7E, CACHE_END - CACHE_MASK)
    code.label("clear_cache")
    code.emit(0xA3, 0xF0)
    code.rel8(0xDE, "clear_cache")
    emit_mov_dptr(code, CACHE_VERSION)
    code.emit(0x74, 0x0B, 0xF0)
    code.emit(0xD0, 0x06)
    code.emit(0x02, 0x15, 0x22)

    code.label("smbus_entry")
    # The original veneer overwrites DPTR but does not consume incoming A.
    # Preserve both while inspecting the doorbell and live input registers.
    code.emit(0xC0, 0xE0, 0xC0, 0x82, 0xC0, 0x83)
    emit_mov_dptr(code, DOORBELL)
    code.emit(0xE0, 0x64, 0x5A)
    code.rel8(0x60, "request_pending")
    code.label("not_matching")
    code.emit(0xD0, 0x83, 0xD0, 0x82, 0xD0, 0xE0)
    code.abs_jump(0x02, "original_smbus_tail")

    code.label("request_pending")
    # If an interrupt reaches the veneer while the selected stock worker is
    # already executing, bypass observer bookkeeping to prevent re-entry.
    emit_mov_dptr(code, CACHE_STATE)
    code.emit(0xE0, 0x64, 0x02)
    code.rel8(0x60, "not_matching")
    code.emit(0xEB, 0x64, 0xA1)  # MOV A,R3; XRL A,#A1
    code.rel8(0x70, "not_matching")

    emit_mov_dptr(code, CACHE_STATE)
    code.emit(0x74, 0x02, 0xF0)
    for register, destination in ((3, CACHE_R3), (5, CACHE_R5), (7, CACHE_R7)):
        emit_mov_dptr(code, destination)
        code.emit(0xE8 + register, 0xF0)  # MOV A,Rn; MOVX @DPTR,A
    for index in range(4):
        emit_copy_xram_byte(code, 0x0A6E + index, CACHE_DESCRIPTOR + index)
    emit_copy_xram_byte(code, 0x0497, CACHE_SESSION)
    emit_copy_xram_byte(code, 0x0490, CACHE_POWER)
    emit_mov_dptr(code, CACHE_FILTER)
    code.emit(0x74, 0x01, 0xF0)

    # Restore the exact input state before executing the displaced raw veneer.
    code.emit(0xD0, 0x83, 0xD0, 0x82, 0xD0, 0xE0)
    code.lcall("original_smbus_call")

    # Preserve complete caller-visible post-call state. MOV A,Rn uses the
    # currently selected register bank; PSW is restored before the inverse
    # MOV Rn,A sequence.
    code.emit(0xC0, 0xE0, 0xC0, 0xF0, 0xC0, 0x82, 0xC0, 0x83)
    # Snapshot the active-bank R7 return before changing PSW to register bank 0.
    emit_mov_dptr(code, CACHE_RETURN_R7)
    code.emit(0xEF, 0xF0)
    for register in range(8):
        code.emit(0xE8 + register, 0xC0, 0xE0)
    code.emit(0xC0, 0xD0, 0x53, 0xD0, 0xE7)

    for source, destination in (
        (0x0A73, CACHE_RESULT),
        (0x0A74, CACHE_HOST),
        (0x0A75, CACHE_COMPLETION),
        (0x00C0, CACHE_DATA),
        (0x00C1, CACHE_DATA + 1),
    ):
        emit_copy_xram_byte(code, source, destination)
    emit_mov_dptr(code, CACHE_MATCH_COUNT)
    code.emit(0xE0, 0x04, 0xF0)
    emit_mov_dptr(code, CACHE_STATE)
    code.emit(0x74, 0x03, 0xF0)
    emit_mov_dptr(code, DOORBELL)
    code.emit(0x74, 0xC3, 0xF0)

    code.emit(0xD0, 0xD0)
    for register in reversed(range(8)):
        code.emit(0xD0, 0xE0, 0xF8 + register)
    code.emit(0xD0, 0x83, 0xD0, 0x82, 0xD0, 0xF0, 0xD0, 0xE0, 0x22)

    code.label("original_smbus_call")
    code.emit(0x90, 0x80, 0xD2)
    code.emit(0x12, 0x11, 0x00)
    code.emit(0x22)

    code.label("original_smbus_tail")
    code.emit(0x90, 0x80, 0xD2)
    code.emit(0x02, 0x11, 0x00)

    return code.finish(), dict(code.labels)


CAVE, CAVE_LABELS = build_cave()
MAIN_HOOK_REPLACEMENT = bytes((0x12, CAVE_LABELS["main_entry"] >> 8, CAVE_LABELS["main_entry"] & 0xFF))
SMBUS_HOOK_REPLACEMENT = bytes((0x02, CAVE_LABELS["smbus_entry"] >> 8, CAVE_LABELS["smbus_entry"] & 0xFF))


def require_bytes(image: bytes, offset: int, expected: bytes, label: str) -> None:
    actual = image[offset : offset + len(expected)]
    if actual != expected:
        raise SystemExit(f"unexpected bytes for {label} at 0x{offset:05X}: {actual.hex(' ')}")


def main() -> int:
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

    require_bytes(source, MAIN_HOOK_OFFSET, MAIN_HOOK_ORIGINAL, "common:0272 main-loop call")
    require_bytes(source, MAIN_HOOK_CONTEXT_OFFSET, MAIN_HOOK_CONTEXT, "common main-loop context")
    require_bytes(source, SMBUS_VENEER_OFFSET, SMBUS_VENEER_ORIGINAL, "common:1B22 SMBus veneer")
    require_bytes(source, SMBUS_VENEER_CONTEXT_OFFSET, SMBUS_VENEER_CONTEXT, "common SMBus veneer context")
    require_bytes(source, ORIGINAL_MAIN_VENEER_OFFSET, ORIGINAL_MAIN_VENEER, "common:1522 original veneer")
    require_bytes(source, COMMON_BANK0_CALL_GATE_OFFSET, COMMON_BANK0_CALL_GATE_PREFIX, "common:1100 bank0 dispatch gate")
    require_bytes(source, TRANSACTION_WORKER_OFFSET, TRANSACTION_WORKER_PREFIX, "bank0:80D2 SMBus worker")
    require_bytes(source, LIVE_BATTERY_CALL_CONTEXT_OFFSET, LIVE_BATTERY_CALL_CONTEXT, "bank1:D9EA live R3=A1 transaction context")
    require_bytes(source, LIVE_BATTERY_CALL_OFFSET, bytes.fromhex("12 1b 22"), "bank1:DA02 live common:1B22 call")
    require_bytes(source, E8A8_OFFSET, E8A8_HELPER, "bank1:E8A8 descriptor tail setting R3=A1")

    if CAVE_LOGICAL + len(CAVE) > CAVE_LIMIT:
        raise SystemExit(f"observer cave exceeds fixed common: end=0x{CAVE_LOGICAL + len(CAVE):04X}, size={len(CAVE)}")
    require_bytes(source, CAVE_OFFSET, b"\xFF" * len(CAVE), "fixed-common code cave")

    for address in range(DOORBELL, CACHE_END + 1):
        pattern = bytes((0x90, address >> 8, address & 0xFF))
        if pattern in source:
            raise SystemExit(f"candidate XRAM address 0x{address:04X} has a direct DPTR reference")

    patched = bytearray(source)
    patched[MAIN_HOOK_OFFSET : MAIN_HOOK_OFFSET + 3] = MAIN_HOOK_REPLACEMENT
    patched[SMBUS_VENEER_OFFSET : SMBUS_VENEER_OFFSET + 3] = SMBUS_HOOK_REPLACEMENT
    patched[CAVE_OFFSET : CAVE_OFFSET + len(CAVE)] = CAVE

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(patched)

    print(f"source sha256:  {digest}")
    print(f"patched sha256: {hashlib.sha256(patched).hexdigest()}")
    print(f"main hook:       full 0x{MAIN_HOOK_OFFSET:05X}-0x{MAIN_HOOK_OFFSET + 2:05X} common:0272 -> common:{CAVE_LABELS['main_entry']:04X}")
    print(f"smbus hook:      full 0x{SMBUS_VENEER_OFFSET:05X}-0x{SMBUS_VENEER_OFFSET + 2:05X} common:1B22 -> common:{CAVE_LABELS['smbus_entry']:04X}")
    print(f"filter:          live SMBus calls with incoming R3=0xA1")
    print(f"cave:            full 0x{CAVE_OFFSET:05X}-0x{CAVE_OFFSET + len(CAVE) - 1:05X} common:{CAVE_LOGICAL:04X}-{CAVE_LOGICAL + len(CAVE) - 1:04X} ({len(CAVE)} bytes)")
    print(f"doorbell/cache:  XRAM 0x{DOORBELL:04X}-0x{CACHE_END:04X}")
    print(f"byte sum:        0x{sum(patched):X}")
    print(f"wrote:           {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
