#!/usr/bin/env python3
"""Build a FlexiCharge v2.2 EC image from the official GXxHXxx_21.200 firmware.

Applies four reviewed, byte-exact patches to the untouched 256 KiB official
image, in this order:

1. direct calibration commit   full 0x14974     skip the +/-300 mAh FCC clamp
2. mode-locked voltage tiers   full 0x1D303     keep only the 0x07A6 mode tier
3. ApExistFlag ignore          full 0x1D1D7     ignore the mode-clear action
4. FlexiCharge v2.2            bank2 + bank0 hooks + fixed-common cave

The source must be the pristine vendor image (sha256 ``34c050d3...``); its digest
pins every original byte.  The output is asserted against the reviewed
FlexiCharge v2.2 digest (``ff668376...``), so a drifted offset or emitter aborts
the build instead of producing an unreviewed image.

This script never touches hardware and never flashes anything -- use the
flashing tool from the ec_reverse project for that.

Usage::

    uv run python firmware_mods/GXxHXxx_21.200/build-from-official-20261103/patch_ec_flexicharge_v22.py \
        GXxHXxx_21.200 flexicharge-v2.2.bin
"""

import argparse
import hashlib
from pathlib import Path


EXPECTED_SIZE = 0x40000
SOURCE_SHA256 = "34c050d30772da07ef262fc7016e0677b9b1b4cdcd90cf43d93f0f15bf6a38c2"
OUTPUT_SHA256 = "ff6683769da655f3cafa364ddc9cfcaad84ad28fa7122191d3fefad00a7a19cf"


# --- patch 1: direct calibration commit -------------------------------------
# State 5 has already computed this round's candidate factor in R7 when the
# stock code clamps it to old_FCC/100 +/- 3 (about +/-300 mAh).  Jump straight
# to the commit block that all branches converge on; 0x4977-0x498D becomes
# unreachable dead code.
CAL_OFFSET = 0x14974                # was 90 03 9b  MOV DPTR,#039B
CAL_REPLACEMENT = bytes.fromhex("02 c9 8e")          # LJMP bank1:C98E


# --- patch 2: mode-locked voltage tiers -------------------------------------
# Replace the bank1:D303 tier selection with a single tier driven by
# 0x07A6[5:4] (0/100/200 mV per cell), then jump to the shared arithmetic
# tail at bank1:D3EC.
MODE_OFFSET = 0x1D303               # was the cycle/stress/auxiliary five-tier compare
MODE_REPLACEMENT = bytes.fromhex(
    "90 07 a6 e0 54 30 "
    "b4 20 05 7b fa 02 d3 ec "
    "b4 10 05 7b 96 02 d3 ec "
    "7b 00 02 d3 ec"
)


# --- patch 3: ignore the ApExistFlag mode-clear action ----------------------
APEXIST_OFFSET = 0x1D1D7            # was 12 df 7e 70 07  LCALL DF7E; JNZ D1E3
APEXIST_REPLACEMENT = bytes.fromhex("80 0a 00 00 00")   # SJMP D1E3 + 3 NOP


# --- patch 4: FlexiCharge v2.2 ----------------------------------------------
# Full-image layout: fixed common 0x00000-0x07FFF; bank2's logical
# 0x8000-0xFFFF window is stored at full offsets 0x18000-0x1FFFF.
DECISION_HOOK_OFFSET = 0x1D729       # was 90 04 ab  MOV DPTR,#04AB
DECISION_HOOK_LOGICAL = 0xD729
PRECLEAR_HOOK_OFFSET = 0x1D73A       # was 90 07 b9  MOV DPTR,#07B9
PRECLEAR_HOOK_LOGICAL = 0xD73A
DECISION_CONTINUE_LOGICAL = 0xD741

CAVE_OFFSET = 0x07E6A
CAVE_LOGICAL = 0x7E6A
CAVE_LIMIT = 0x08000

# bank0:DE54 is LCALL bank0:DE03 in the cold-start restore sequence. The
# replacement calls a fixed-common wrapper which preserves that stock call and
# then restores the persistent FlexiCharge low threshold.
RESTORE_HOOK_OFFSET = 0x0DE54        # was 12 de 03  LCALL bank0:DE03
RESTORE_HOOK_LOGICAL = 0xDE54
RESTORE_STOCK_TARGET = 0xDE03

HIGH_LIMIT = 0x07B9
LOW_LIMIT = 0x07D0
RSOC = 0x04AB
PHASE_BIT = 0x80
LOW_SHADOW = 0x0773
SAVE_MAGIC_0 = 0x077E
SAVE_MAGIC_1 = 0x077F


class Code:
    """Minimal 8051 assembler with rel8/abs16 fixups for the fixed-common cave."""

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

    code.label("restore_entry")
    # This hook runs while bank0 is selected. Preserve the original bank0:DE03
    # call before applying the extension.
    code.abs16(0x12, RESTORE_STOCK_TARGET)
    mov_dptr(code, LOW_SHADOW)
    code.emit(0xE0)                       # MOVX A,@DPTR
    code.bit_rel8(0x30, 0xE7, "restore_done")  # no v2.2 record yet
    code.emit(0x54, 0x7F)                 # discard persistent valid marker
    mov_dptr(code, LOW_LIMIT)
    code.emit(0xF0)                       # restore low with phase=0
    code.label("restore_done")
    code.emit(0x22)

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
    code.emit(0xF5, 0xF0)                 # MOV B,A

    # Persist threshold changes, but never persist the volatile phase latch.
    # 0x0773 is part of the stock 16-byte NVM-mapped block and has no direct
    # consumer in this firmware. bit7 distinguishes our record from defaults.
    mov_dptr(code, LOW_SHADOW)
    code.emit(0xE0)
    code.bit_rel8(0x30, 0xE7, "update_low_shadow")
    code.emit(0x54, 0x7F, 0x65, 0xF0)        # (shadow & 0x7f) XOR B
    code.rel8(0x60, "low_shadow_current")
    code.label("update_low_shadow")
    code.emit(0xE5, 0xF0, 0x44, 0x80, 0xF0)  # shadow = 0x80 | low
    mov_dptr(code, SAVE_MAGIC_0)
    code.emit(0x74, 0xA5, 0xF0, 0xA3, 0x74, 0x78, 0xF0)
    code.label("low_shadow_current")
    code.emit(0xE5, 0xF0)                 # MOV A,B
    code.rel8(0x60, "invalid_pair")       # low==0 disables v2

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
RESTORE_HOOK_REPLACEMENT = bytes((
    0x12,
    CAVE_LABELS["restore_entry"] >> 8,
    CAVE_LABELS["restore_entry"] & 0xFF,
))


def patch_image(source: bytes) -> bytes:
    if len(source) != EXPECTED_SIZE or hashlib.sha256(source).hexdigest() != SOURCE_SHA256:
        raise SystemExit(
            f"refusing unknown image: size=0x{len(source):X}, "
            f"sha256={hashlib.sha256(source).hexdigest()}"
        )
    if CAVE_LOGICAL + len(CAVE) > CAVE_LIMIT:
        raise SystemExit(
            f"FlexiCharge v2.2 cave exceeds fixed common: "
            f"end=0x{CAVE_LOGICAL + len(CAVE):04X}, size={len(CAVE)}"
        )

    patched = bytearray(source)
    patched[CAL_OFFSET : CAL_OFFSET + len(CAL_REPLACEMENT)] = CAL_REPLACEMENT
    patched[MODE_OFFSET : MODE_OFFSET + len(MODE_REPLACEMENT)] = MODE_REPLACEMENT
    patched[APEXIST_OFFSET : APEXIST_OFFSET + len(APEXIST_REPLACEMENT)] = APEXIST_REPLACEMENT
    patched[DECISION_HOOK_OFFSET : DECISION_HOOK_OFFSET + 3] = DECISION_HOOK_REPLACEMENT
    patched[PRECLEAR_HOOK_OFFSET : PRECLEAR_HOOK_OFFSET + 3] = PRECLEAR_HOOK_REPLACEMENT
    patched[RESTORE_HOOK_OFFSET : RESTORE_HOOK_OFFSET + 3] = RESTORE_HOOK_REPLACEMENT
    patched[CAVE_OFFSET : CAVE_OFFSET + len(CAVE)] = CAVE
    patched = bytes(patched)

    # The source digest pins every original byte and the output digest pins every
    # replacement, so a drifted offset or emitter aborts instead of emitting an
    # unreviewed image.
    digest = hashlib.sha256(patched).hexdigest()
    if digest != OUTPUT_SHA256:
        raise SystemExit(
            f"refusing unreviewed image: expected sha256 {OUTPUT_SHA256}, got {digest}"
        )
    return patched


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="pristine official GXxHXxx_21.200 image")
    parser.add_argument("output", type=Path, help="patched FlexiCharge v2.2 image")
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
    print(f"restore hook:  bank0:0x{RESTORE_HOOK_LOGICAL:04X} -> fixed-common:0x{CAVE_LABELS['restore_entry']:04X}")
    print(f"cave:   fixed-common 0x{CAVE_LOGICAL:04X}-0x{CAVE_LOGICAL + len(CAVE) - 1:04X} ({len(CAVE)} bytes)")
    print("phase:  EC[0x07D0].bit7=1 means a low-started charge cycle is active")
    print("persist: EC[0x0773]=0x80|low; stock 0x077E=A5/0x077F=78 commit handshake")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
