import hashlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH_PATH = ROOT / "firmware_mods/GXxHXxx_21.200/cal-direct-mode-locked-apexist-ignore-flexicharge-20260727/patch_flexicharge.py"
BASE_PATH = ROOT / "firmware_mods/GXxHXxx_21.200/cal-direct-mode-locked-apexist-ignore-20260723/GXxHXxx_21.200-cal-direct-mode-locked-apexist-ignore-20260723.bin"
OUTPUT_PATH = ROOT / "firmware_mods/GXxHXxx_21.200/cal-direct-mode-locked-apexist-ignore-flexicharge-20260727/GXxHXxx_21.200-cal-direct-mode-locked-apexist-ignore-flexicharge-20260727.bin"

spec = importlib.util.spec_from_file_location("patch_flexicharge", PATCH_PATH)
PATCH = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PATCH)


def test_source_identity_and_raw_bank2_control_flow():
    source = BASE_PATH.read_bytes()
    assert len(source) == PATCH.EXPECTED_SIZE
    assert hashlib.sha256(source).hexdigest() == PATCH.EXPECTED_SHA256
    assert source[PATCH.DECISION_CONTEXT_OFFSET : PATCH.DECISION_CONTEXT_OFFSET + len(PATCH.DECISION_CONTEXT)] == PATCH.DECISION_CONTEXT

    # Decode from raw opcodes, not d52 labels: bank2:D729 begins the RSOC/high
    # comparison, and the untouched continuation is bank2:D741.
    assert source[PATCH.DECISION_HOOK_OFFSET : PATCH.DECISION_HOOK_OFFSET + 24] == bytes.fromhex(
        "90 04 ab e0 c3 9b 90 07 b9 e0 40 09 44 80 f0 80 07 "
        "90 07 b9 e0 54 7f f0"
    )
    assert PATCH.DECISION_HOOK_OFFSET == 0x18000 + (PATCH.DECISION_HOOK_LOGICAL - 0x8000)
    assert PATCH.DECISION_HOOK_LOGICAL >= 0x8000
    assert PATCH.CAVE_LOGICAL < 0x8000


def test_patch_redirects_only_the_compare_entry_and_fixed_common_cave():
    source = BASE_PATH.read_bytes()
    patched = PATCH.patch_image(source)
    assert patched[PATCH.DECISION_HOOK_OFFSET : PATCH.DECISION_HOOK_OFFSET + 3] == bytes.fromhex("02 7e 6a")
    assert patched[PATCH.CAVE_OFFSET : PATCH.CAVE_OFFSET + len(PATCH.CAVE)] == PATCH.CAVE

    changed = [i for i, (a, b) in enumerate(zip(source, patched)) if a != b]
    allowed = set(range(PATCH.DECISION_HOOK_OFFSET, PATCH.DECISION_HOOK_OFFSET + 3))
    allowed.update(range(PATCH.CAVE_OFFSET, PATCH.CAVE_OFFSET + len(PATCH.CAVE)))
    assert set(changed) <= allowed


def test_cave_contains_read_only_policy_logic_and_stock_continuation():
    cave = PATCH.CAVE
    assert PATCH.CAVE_LOGICAL + len(cave) <= PATCH.CAVE_LIMIT
    assert bytes.fromhex("90 04 ab e0 c3 9b") in cave
    assert bytes.fromhex("90 07 b9 e0 30 e7") in cave
    assert bytes.fromhex("90 07 d0 e0 54 7f") in cave
    assert bytes.fromhex("c3 94 60") in cave  # lower <= 95 validation
    assert bytes.fromhex("eb c3 95 f0") in cave  # high - low
    assert cave.count(bytes.fromhex("02 d7 41")) == 3

    # The firmware patch never talks to the battery/charger SMBus directly and
    # writes only the stock 0x07B9 policy byte.
    assert bytes.fromhex("90 07 d0 f0") not in cave
    assert bytes.fromhex("90 08 50") not in cave
    assert bytes.fromhex("90 04 32") not in cave
    assert bytes.fromhex("90 05 74") not in cave


def test_generated_binary_matches_patch_function():
    expected = PATCH.patch_image(BASE_PATH.read_bytes())
    assert OUTPUT_PATH.read_bytes() == expected



def run_cave(rsoc, low, high, stopped):
    code = PATCH.CAVE
    xram = {
        PATCH.RSOC: rsoc,
        PATCH.LOW_LIMIT: low,
        PATCH.HIGH_LIMIT: high | (0x80 if stopped else 0),
    }
    pc = 0
    a = b = dptr = carry = 0
    stack = []

    def rel(value):
        return value - 0x100 if value & 0x80 else value

    for _ in range(200):
        op = code[pc]
        if op == 0x90:
            dptr = (code[pc + 1] << 8) | code[pc + 2]
            pc += 3
        elif op == 0xE0:
            a = xram.get(dptr, 0)
            pc += 1
        elif op == 0xF0:
            xram[dptr] = a
            pc += 1
        elif op == 0xC3:
            carry = 0
            pc += 1
        elif op == 0xD3:
            carry = 1
            pc += 1
        elif op == 0x9B:
            value = a - high - carry
            carry = int(value < 0)
            a = value & 0xFF
            pc += 1
        elif op == 0x95 and code[pc + 1] == 0xF0:
            value = a - b - carry
            carry = int(value < 0)
            a = value & 0xFF
            pc += 2
        elif op == 0x94:
            value = a - code[pc + 1] - carry
            carry = int(value < 0)
            a = value & 0xFF
            pc += 2
        elif op == 0x50:
            pc = pc + 2 + (rel(code[pc + 1]) if carry == 0 else 0)
        elif op == 0x40:
            pc = pc + 2 + (rel(code[pc + 1]) if carry else 0)
        elif op == 0x60:
            pc = pc + 2 + (rel(code[pc + 1]) if a == 0 else 0)
        elif op == 0x80:
            pc = pc + 2 + rel(code[pc + 1])
        elif op == 0x30 and code[pc + 1] == 0xE7:
            pc = pc + 3 + (rel(code[pc + 2]) if not (a & 0x80) else 0)
        elif op == 0x54:
            a &= code[pc + 1]
            pc += 2
        elif op == 0x44:
            a |= code[pc + 1]
            pc += 2
        elif op == 0xC0 and code[pc + 1] == 0xF0:
            stack.append(b)
            pc += 2
        elif op == 0xD0 and code[pc + 1] == 0xF0:
            b = stack.pop()
            pc += 2
        elif op == 0xF5 and code[pc + 1] == 0xF0:
            b = a
            pc += 2
        elif op == 0xEB:
            a = high
            pc += 1
        elif op == 0x02:
            target = (code[pc + 1] << 8) | code[pc + 2]
            assert target == PATCH.DECISION_CONTINUE_LOGICAL
            assert not stack
            return bool(xram[PATCH.HIGH_LIMIT] & 0x80)
        else:
            raise AssertionError(f"unsupported opcode 0x{op:02X} at cave+0x{pc:X}")
    raise AssertionError("cave did not terminate")


def test_cave_boundary_behavior_emulation():
    # Charging below the upper threshold remains allowed until the stop bit has
    # first been asserted at the upper threshold.
    assert run_cave(rsoc=79, low=40, high=80, stopped=False) is False
    assert run_cave(rsoc=80, low=40, high=80, stopped=False) is True

    # Once stopped, the policy holds across the window and resumes at <= low.
    assert run_cave(rsoc=79, low=40, high=80, stopped=True) is True
    assert run_cave(rsoc=41, low=40, high=80, stopped=True) is True
    assert run_cave(rsoc=40, low=40, high=80, stopped=True) is False
    assert run_cave(rsoc=39, low=40, high=80, stopped=True) is False

    # Disabled or malformed lower limits fall back to stock upper-only behavior.
    assert run_cave(rsoc=79, low=0, high=80, stopped=True) is False
    assert run_cave(rsoc=79, low=80, high=80, stopped=True) is False
    assert run_cave(rsoc=79, low=96, high=99, stopped=True) is False
