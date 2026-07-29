#!/usr/bin/env python3
"""Implement stateful EC FlexiCharge v2.1 using 0x07B9 and 0x07D0.

0x07B9[6:0] remains the upper threshold and 0x07B9.bit7 remains the stock
charger-inhibit policy bit.  0x07D0[6:0] is the lower threshold; because the
stock EC never references 0x07D0, v2 uses 0x07D0.bit7 as a volatile
charge-cycle phase latch:

* phase=0: hold charging inhibited while SOC is above low;
* SOC<=low: set phase=1 and permit a charge cycle;
* phase=1: continue the cycle across adapter unplug/replug;
* SOC>=high: clear phase and assert the stock stop bit.

Both bank2:D729 (normal upper-limit decision) and bank2:D73A (stock gate-fail
clear entry) are redirected so transient adapter/session gates cannot destroy
the phase decision. low=0/high>0 remains compatible with the original upper-only behavior.
high=0 and other invalid pairs are treated as disabled/unrestricted: stop and
phase are cleared without erasing the low seven threshold bits.
"""

import argparse
import hashlib
from pathlib import Path

EXPECTED_SIZE = 0x40000
EXPECTED_SHA256 = "aa4a52ab7bbeff22e38b0845bc627b735c7b7e6b28ca6868093454a5696dd5d5"

# Full-image layout: fixed common 0x00000-0x07FFF; bank2's logical
# 0x8000-0xFFFF window is stored at full offsets 0x18000-0x1FFFF.
DECISION_HOOK_OFFSET = 0x1D729
DECISION_HOOK_LOGICAL = 0xD729
PRECLEAR_HOOK_OFFSET = 0x1D73A
PRECLEAR_HOOK_LOGICAL = 0xD73A
DECISION_CONTINUE_LOGICAL = 0xD741
DECISION_CONTEXT_OFFSET = 0x1D706
DECISION_CONTEXT = bytes.fromhex(
    "90 07 b9 e0 54 7f ff cb ef cb "
    "12 df 87 60 25 "
    "e0 54 04 60 20 "
    "cf cf ef fd c3 "
    "12 df cf 50 16 "
    "ed 70 02 80 11 "
    "90 04 ab e0 c3 9b "
    "90 07 b9 e0 40 09 44 80 f0 80 07 "
    "90 07 b9 e0 54 7f f0 "
    "90 08 7f e0 6b"
)

CAVE_OFFSET = 0x07E6A
CAVE_LOGICAL = 0x7E6A
CAVE_LIMIT = 0x08000

HIGH_LIMIT = 0x07B9
LOW_LIMIT = 0x07D0
RSOC = 0x04AB
PHASE_BIT = 0x80


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

    def bit_rel8(self, opcode, bit_address, target):
        self.emit(opcode, bit_address, 0)
        self.rel8_fixups.append((len(self.data) - 1, target))

    def abs16(self, opcode, target):
        self.emit(opcode, 0, 0)
        self.abs16_fixups.append((len(self.data) - 2, target))

    def finish(self):
        for operand_index, target_name in self.rel8_fixups:
            target = self.labels[target_name]
            next_pc = self.origin + operand_index + 1
            displacement = target - next_pc
            if not -128 <= displacement <= 127:
                raise ValueError(f"relative target {target_name} out of range: {displacement}")
            self.data[operand_index] = displacement & 0xFF
        for operand_index, target in self.abs16_fixups:
            if isinstance(target, str):
                target = self.labels[target]
            self.data[operand_index] = target >> 8
            self.data[operand_index + 1] = target & 0xFF
        return bytes(self.data)


def mov_dptr(code: Code, address: int):
    code.emit(0x90, address >> 8, address & 0xFF)


def build_cave():
    code = Code(CAVE_LOGICAL)

    code.label("decision_entry")
    # apply_policy returns:
    #   A=1: valid v2 low/high pair; phase and stop bit already updated.
    #   A=0: official upper-only compatibility (low==0, high>0); run stock D729.
    #   A=2: disabled/invalid/unrestricted; stop+phase cleared, skip stock D729.
    code.abs16(0x12, "apply_policy")
    code.emit(0x24, 0xFF)                 # A=1 -> zero, A=0 -> 0xff, A=2 -> 1
    code.rel8(0x60, "decision_valid")     # JZ: valid v2 pair
    code.emit(0x04)                       # A=0 -> zero; A=2 -> 2
    code.rel8(0x60, "stock_decision")     # JZ: upper-only compatibility

    # Disabled/invalid: do not let a nonsensical high value act as an upper
    # limit. Preserve D741's compare-style carry contract using SOC-high.
    mov_dptr(code, RSOC)
    code.emit(0xE0, 0xC3, 0x9B)
    code.abs16(0x02, DECISION_CONTINUE_LOGICAL)

    code.label("decision_valid")
    # Recreate the stock D729 comparison solely to preserve its carry/PSW
    # contract at the untouched D741 continuation. A is overwritten at D743.
    mov_dptr(code, RSOC)
    code.emit(0xE0, 0xC3, 0x9B)          # SOC - high(R3)
    code.abs16(0x02, DECISION_CONTINUE_LOGICAL)

    code.label("stock_decision")
    mov_dptr(code, RSOC)
    code.emit(0xE0, 0xC3, 0x9B)
    mov_dptr(code, HIGH_LIMIT)
    code.emit(0xE0)
    code.rel8(0x40, "stock_clear")        # JC: SOC < high
    code.emit(0x44, 0x80, 0xF0)           # set stop at/above high
    code.abs16(0x02, DECISION_CONTINUE_LOGICAL)

    code.label("stock_clear")
    code.emit(0x54, 0x7F, 0xF0)           # clear stop below high
    code.abs16(0x02, DECISION_CONTINUE_LOGICAL)

    code.label("preclear_entry")
    # Original D73A does not alter PSW. Preserve it around the state machine.
    code.emit(0xC0, 0xD0)                 # PUSH PSW
    code.abs16(0x12, "apply_policy")
    code.emit(0x24, 0xFE)                 # A=2 -> zero when disabled/invalid
    code.rel8(0x60, "preclear_done")      # disabled/invalid already cleared
    code.emit(0x04)                       # A=1 -> zero when valid v2 pair
    code.rel8(0x60, "preclear_done")      # valid pair already applied

    # A=0: official upper-only compatibility retains exact stock D73A behavior.
    mov_dptr(code, HIGH_LIMIT)
    code.emit(0xE0, 0x54, 0x7F, 0xF0)

    code.label("preclear_done")
    code.emit(0xD0, 0xD0)                 # POP PSW
    code.abs16(0x02, DECISION_CONTINUE_LOGICAL)

    code.label("apply_policy")
    # B is caller-owned. Save raw 0x07D0 separately on the stack so B can hold
    # the masked lower threshold while bit7 remains available as phase state.
    code.emit(0xC0, 0xF0)                 # PUSH B
    mov_dptr(code, LOW_LIMIT)
    code.emit(0xE0, 0xC0, 0xE0)           # MOVX A,@DPTR; PUSH ACC (raw low)
    code.emit(0x54, 0x7F)                 # low &= 0x7f
    code.rel8(0x60, "invalid_pair")       # low==0 disables v2
    code.emit(0xF5, 0xF0)                 # MOV B,A

    code.emit(0xEB)                       # high == 0 means unrestricted/disabled
    code.rel8(0x60, "disabled_pair")

    code.emit(0xE5, 0xF0)                 # MOV A,B (low)
    code.emit(0xC3, 0x94, 0x60)           # low - 96
    code.rel8(0x50, "disabled_pair")      # low >= 96

    code.emit(0xEB, 0xC3, 0x95, 0xF0)     # high(R3) - low(B)
    code.rel8(0x40, "disabled_pair")      # high < low
    code.rel8(0x60, "disabled_pair")      # high == low

    # Threshold transitions take priority over the old phase latch.
    mov_dptr(code, RSOC)
    code.emit(0xE0, 0xC3, 0x95, 0xF0)     # SOC - low
    code.rel8(0x40, "at_or_below_low")
    code.rel8(0x60, "at_or_below_low")

    mov_dptr(code, RSOC)
    code.emit(0xE0, 0xC3, 0x9B)           # SOC - high
    code.rel8(0x50, "at_or_above_high")

    # Middle band: raw low.bit7 records whether this charge cycle began at/below
    # low. This is what distinguishes a first plug from an interrupted cycle.
    code.emit(0xD0, 0xE0)                 # POP ACC (raw low)
    code.bit_rel8(0x30, 0xE7, "middle_hold")  # JNB ACC.7: no active cycle

    code.label("middle_charge")
    mov_dptr(code, HIGH_LIMIT)
    code.emit(0xE0, 0x54, 0x7F, 0xF0)     # keep charging: clear stock stop
    code.rel8(0x80, "valid_return")

    code.label("middle_hold")
    mov_dptr(code, HIGH_LIMIT)
    code.emit(0xE0, 0x44, 0x80, 0xF0)     # first plug/hold: assert stop
    code.rel8(0x80, "valid_return")

    code.label("at_or_below_low")
    code.emit(0xD0, 0xE0, 0x44, 0x80)     # POP ACC; phase=1
    mov_dptr(code, LOW_LIMIT)
    code.emit(0xF0)
    mov_dptr(code, HIGH_LIMIT)
    code.emit(0xE0, 0x54, 0x7F, 0xF0)     # permit charging
    code.rel8(0x80, "valid_return")

    code.label("at_or_above_high")
    code.emit(0xD0, 0xE0, 0x54, 0x7F)     # POP ACC; phase=0
    mov_dptr(code, LOW_LIMIT)
    code.emit(0xF0)
    mov_dptr(code, HIGH_LIMIT)
    code.emit(0xE0, 0x44, 0x80, 0xF0)     # inhibit charging

    code.label("valid_return")
    code.emit(0xD0, 0xF0)                 # POP B
    code.emit(0x74, 0x01, 0x22)           # MOV A,#1; RET

    code.label("invalid_pair")
    # low==0 with a nonzero high is the official upper-only mode. Do not touch
    # 0x07D0.bit7 here, so host software that preserves bit7 is not surprised.
    code.emit(0xEB)                       # high==0 is canonical disabled 0/100
    code.rel8(0x60, "disabled_pair")
    code.emit(0xD0, 0xE0)                 # discard saved raw low
    code.emit(0xD0, 0xF0)                 # POP B
    code.emit(0xE4, 0x22)                 # A=0: stock upper-only fallback

    code.label("disabled_pair")
    # Disabled/unrestricted behavior: clear runtime stop and phase bits, but do
    # not erase the low seven threshold bits. This avoids destroying transient
    # two-step host writes while ensuring invalid high/low pairs cannot inhibit
    # charging via a bogus upper-only limit.
    code.emit(0xD0, 0xE0, 0x54, 0x7F)     # POP ACC; clear phase bit only
    mov_dptr(code, LOW_LIMIT)
    code.emit(0xF0)
    mov_dptr(code, HIGH_LIMIT)
    code.emit(0xE0, 0x54, 0x7F, 0xF0)     # clear stop bit only
    code.emit(0xD0, 0xF0)                 # POP B
    code.emit(0x74, 0x02, 0x22)           # A=2: disabled/invalid handled

    return code.finish(), code.labels


CAVE, CAVE_LABELS = build_cave()
DECISION_HOOK_REPLACEMENT = bytes((
    0x02,
    CAVE_LABELS["decision_entry"] >> 8,
    CAVE_LABELS["decision_entry"] & 0xFF,
))
PRECLEAR_HOOK_REPLACEMENT = bytes((
    0x02,
    CAVE_LABELS["preclear_entry"] >> 8,
    CAVE_LABELS["preclear_entry"] & 0xFF,
))


def require_bytes(source: bytes, offset: int, expected: bytes, description: str):
    actual = source[offset : offset + len(expected)]
    if actual != expected:
        raise SystemExit(
            f"{description} differs at full offset 0x{offset:05X}: "
            f"expected {expected.hex(' ')}, got {actual.hex(' ')}"
        )


def patch_image(source: bytes) -> bytes:
    digest = hashlib.sha256(source).hexdigest()
    if len(source) != EXPECTED_SIZE or digest != EXPECTED_SHA256:
        raise SystemExit(
            f"refusing unknown image: size=0x{len(source):X}, sha256={digest}"
        )
    if CAVE_LOGICAL + len(CAVE) > CAVE_LIMIT:
        raise SystemExit(
            f"FlexiCharge v2.1 cave exceeds fixed common: "
            f"end=0x{CAVE_LOGICAL + len(CAVE):04X}, size={len(CAVE)}"
        )

    require_bytes(source, DECISION_CONTEXT_OFFSET, DECISION_CONTEXT, "bank2 charge-limit decision context")
    require_bytes(source, DECISION_HOOK_OFFSET, bytes.fromhex("90 04 ab"), "bank2:D729 decision hook")
    require_bytes(source, PRECLEAR_HOOK_OFFSET, bytes.fromhex("90 07 b9"), "bank2:D73A preclear hook")
    require_bytes(source, CAVE_OFFSET, b"\xFF" * len(CAVE), "fixed-common code cave")

    patched = bytearray(source)
    patched[DECISION_HOOK_OFFSET : DECISION_HOOK_OFFSET + 3] = DECISION_HOOK_REPLACEMENT
    patched[PRECLEAR_HOOK_OFFSET : PRECLEAR_HOOK_OFFSET + 3] = PRECLEAR_HOOK_REPLACEMENT
    patched[CAVE_OFFSET : CAVE_OFFSET + len(CAVE)] = CAVE
    return bytes(patched)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.source.resolve() == args.output.resolve():
        raise SystemExit("refusing to overwrite source image")

    source = args.source.read_bytes()
    patched = patch_image(source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(patched)
    print(f"wrote:  {args.output}")
    print(f"size:   {len(patched)}")
    print(f"sha256: {hashlib.sha256(patched).hexdigest()}")
    print(f"decision hook: bank2:0x{DECISION_HOOK_LOGICAL:04X} -> fixed-common:0x{CAVE_LABELS['decision_entry']:04X}")
    print(f"preclear hook: bank2:0x{PRECLEAR_HOOK_LOGICAL:04X} -> fixed-common:0x{CAVE_LABELS['preclear_entry']:04X}")
    print(f"cave:   fixed-common 0x{CAVE_LOGICAL:04X}-0x{CAVE_LOGICAL + len(CAVE) - 1:04X} ({len(CAVE)} bytes)")
    print("phase:  EC[0x07D0].bit7=1 means a low-started charge cycle is active")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
