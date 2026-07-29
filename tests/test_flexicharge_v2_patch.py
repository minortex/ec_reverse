import hashlib
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATCH_PATH = ROOT / "firmware_mods/GXxHXxx_21.200/cal-direct-mode-locked-apexist-ignore-flexicharge-v2-20260727/patch_flexicharge_v2.py"
BASE_PATH = ROOT / "firmware_mods/GXxHXxx_21.200/cal-direct-mode-locked-apexist-ignore-20260723/GXxHXxx_21.200-cal-direct-mode-locked-apexist-ignore-20260723.bin"
OUTPUT_PATH = ROOT / "firmware_mods/GXxHXxx_21.200/cal-direct-mode-locked-apexist-ignore-flexicharge-v2-20260727/GXxHXxx_21.200-cal-direct-mode-locked-apexist-ignore-flexicharge-v2-20260727.bin"

spec = importlib.util.spec_from_file_location("patch_flexicharge_v2", PATCH_PATH)
PATCH = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PATCH)


def test_raw_bank2_control_flow_and_mapping():
    source = BASE_PATH.read_bytes()
    assert len(source) == PATCH.EXPECTED_SIZE
    assert hashlib.sha256(source).hexdigest() == PATCH.EXPECTED_SHA256
    assert source[
        PATCH.DECISION_CONTEXT_OFFSET : PATCH.DECISION_CONTEXT_OFFSET + len(PATCH.DECISION_CONTEXT)
    ] == PATCH.DECISION_CONTEXT

    # Decode targets directly from original opcodes. Four gate failures bypass
    # D729 and converge on D73A, which is why both entries must be hooked.
    branches = {
        0xD713: (bytes.fromhex("60 25"), 0xD73A),
        0xD718: (bytes.fromhex("60 20"), 0xD73A),
        0xD722: (bytes.fromhex("50 16"), 0xD73A),
        0xD727: (bytes.fromhex("80 11"), 0xD73A),
    }
    for logical, (expected, target) in branches.items():
        offset = 0x18000 + (logical - 0x8000)
        actual = source[offset : offset + 2]
        assert actual == expected
        displacement = actual[1] - 0x100 if actual[1] & 0x80 else actual[1]
        assert logical + 2 + displacement == target

    assert PATCH.DECISION_HOOK_OFFSET == 0x18000 + (PATCH.DECISION_HOOK_LOGICAL - 0x8000)
    assert PATCH.PRECLEAR_HOOK_OFFSET == 0x18000 + (PATCH.PRECLEAR_HOOK_LOGICAL - 0x8000)
    assert PATCH.CAVE_LOGICAL < 0x8000


def test_stock_firmware_has_no_direct_07d0_consumer():
    source = BASE_PATH.read_bytes()
    assert source.count(bytes.fromhex("90 07 d0")) == 0


def test_patch_redirects_only_two_bank2_entries_and_fixed_common_cave():
    source = BASE_PATH.read_bytes()
    patched = PATCH.patch_image(source)

    assert patched[
        PATCH.DECISION_HOOK_OFFSET : PATCH.DECISION_HOOK_OFFSET + 3
    ] == PATCH.DECISION_HOOK_REPLACEMENT == bytes.fromhex("02 7e 6a")
    assert patched[
        PATCH.PRECLEAR_HOOK_OFFSET : PATCH.PRECLEAR_HOOK_OFFSET + 3
    ] == PATCH.PRECLEAR_HOOK_REPLACEMENT
    assert PATCH.PRECLEAR_HOOK_REPLACEMENT == bytes((
        0x02,
        PATCH.CAVE_LABELS["preclear_entry"] >> 8,
        PATCH.CAVE_LABELS["preclear_entry"] & 0xFF,
    ))
    assert patched[PATCH.CAVE_OFFSET : PATCH.CAVE_OFFSET + len(PATCH.CAVE)] == PATCH.CAVE

    changed = {i for i, (old, new) in enumerate(zip(source, patched)) if old != new}
    allowed = set(range(PATCH.DECISION_HOOK_OFFSET, PATCH.DECISION_HOOK_OFFSET + 3))
    allowed.update(range(PATCH.PRECLEAR_HOOK_OFFSET, PATCH.PRECLEAR_HOOK_OFFSET + 3))
    allowed.update(range(PATCH.CAVE_OFFSET, PATCH.CAVE_OFFSET + len(PATCH.CAVE)))
    assert changed <= allowed


def test_cave_is_policy_only_and_returns_to_untouched_continuation():
    cave = PATCH.CAVE
    assert PATCH.CAVE_LOGICAL + len(cave) <= PATCH.CAVE_LIMIT
    assert bytes.fromhex("90 07 d0 e0 c0 e0 54 7f") in cave
    assert bytes.fromhex("90 04 ab e0 c3 95 f0") in cave
    assert bytes.fromhex("90 04 ab e0 c3 9b") in cave
    assert bytes.fromhex("90 07 d0 f0") in cave  # phase transitions only
    assert cave.count(bytes.fromhex("02 d7 41")) == 4

    # No direct Gauge or charger SMBus transaction is introduced.
    assert bytes.fromhex("90 08 50") not in cave
    assert bytes.fromhex("90 04 32") not in cave
    assert bytes.fromhex("90 05 74") not in cave
    assert bytes.fromhex("12 1b 22") not in cave


def test_generated_binary_matches_patch_function():
    expected = PATCH.patch_image(BASE_PATH.read_bytes())
    assert OUTPUT_PATH.read_bytes() == expected


def run_cave(entry, *, rsoc, low, high, phase, stopped, incoming_carry=0):
    code = PATCH.CAVE
    xram = {
        PATCH.RSOC: rsoc,
        PATCH.LOW_LIMIT: (low & 0x7F) | (0x80 if phase else 0),
        PATCH.HIGH_LIMIT: (high & 0x7F) | (0x80 if stopped else 0),
    }
    pc = PATCH.CAVE_LABELS[entry] - PATCH.CAVE_LOGICAL
    a = b = dptr = 0
    carry = incoming_carry
    data_stack = []
    call_stack = []

    def rel(value):
        return value - 0x100 if value & 0x80 else value

    def branch8(length, take):
        nonlocal pc
        displacement = rel(code[pc + length - 1])
        pc += length + (displacement if take else 0)

    for _ in range(500):
        op = code[pc]
        if op == 0x90:  # MOV DPTR,#imm16
            dptr = (code[pc + 1] << 8) | code[pc + 2]
            pc += 3
        elif op == 0xE0:  # MOVX A,@DPTR
            a = xram.get(dptr, 0)
            pc += 1
        elif op == 0xF0:  # MOVX @DPTR,A
            xram[dptr] = a
            pc += 1
        elif op == 0xE4:  # CLR A
            a = 0
            pc += 1
        elif op == 0x74:  # MOV A,#imm
            a = code[pc + 1]
            pc += 2
        elif op == 0xC3:  # CLR C
            carry = 0
            pc += 1
        elif op == 0x9B:  # SUBB A,R3
            value = a - high - carry
            carry = int(value < 0)
            a = value & 0xFF
            pc += 1
        elif op == 0x95 and code[pc + 1] == 0xF0:  # SUBB A,B
            value = a - b - carry
            carry = int(value < 0)
            a = value & 0xFF
            pc += 2
        elif op == 0x94:  # SUBB A,#imm
            value = a - code[pc + 1] - carry
            carry = int(value < 0)
            a = value & 0xFF
            pc += 2
        elif op == 0x50:  # JNC
            branch8(2, carry == 0)
        elif op == 0x40:  # JC
            branch8(2, carry != 0)
        elif op == 0x60:  # JZ
            branch8(2, a == 0)
        elif op == 0x70:  # JNZ
            branch8(2, a != 0)
        elif op == 0x80:  # SJMP
            branch8(2, True)
        elif op == 0x30 and code[pc + 1] == 0xE7:  # JNB ACC.7
            branch8(3, not (a & 0x80))
        elif op == 0x54:  # ANL A,#imm
            a &= code[pc + 1]
            pc += 2
        elif op == 0x44:  # ORL A,#imm
            a |= code[pc + 1]
            pc += 2
        elif op == 0xC0:  # PUSH direct
            direct = code[pc + 1]
            if direct == 0xD0:
                data_stack.append(("psw", carry))
            elif direct == 0xF0:
                data_stack.append(("b", b))
            elif direct == 0xE0:
                data_stack.append(("acc", a))
            else:
                raise AssertionError(f"unsupported PUSH direct 0x{direct:02X}")
            pc += 2
        elif op == 0xD0:  # POP direct
            direct = code[pc + 1]
            kind, value = data_stack.pop()
            if direct == 0xD0:
                assert kind == "psw"
                carry = value
            elif direct == 0xF0:
                assert kind == "b"
                b = value
            elif direct == 0xE0:
                assert kind == "acc"
                a = value
            else:
                raise AssertionError(f"unsupported POP direct 0x{direct:02X}")
            pc += 2
        elif op == 0xF5 and code[pc + 1] == 0xF0:  # MOV B,A
            b = a
            pc += 2
        elif op == 0xEB:  # MOV A,R3
            a = high
            pc += 1
        elif op == 0x12:  # LCALL addr16
            target = (code[pc + 1] << 8) | code[pc + 2]
            assert PATCH.CAVE_LOGICAL <= target < PATCH.CAVE_LOGICAL + len(code)
            call_stack.append(pc + 3)
            pc = target - PATCH.CAVE_LOGICAL
        elif op == 0x22:  # RET
            pc = call_stack.pop()
        elif op == 0x02:  # LJMP addr16
            target = (code[pc + 1] << 8) | code[pc + 2]
            assert target == PATCH.DECISION_CONTINUE_LOGICAL
            assert not call_stack
            assert not data_stack
            return {
                "phase": bool(xram[PATCH.LOW_LIMIT] & 0x80),
                "stopped": bool(xram[PATCH.HIGH_LIMIT] & 0x80),
                "low": xram[PATCH.LOW_LIMIT] & 0x7F,
                "high": xram[PATCH.HIGH_LIMIT] & 0x7F,
                "carry": carry,
            }
        else:
            raise AssertionError(f"unsupported opcode 0x{op:02X} at cave+0x{pc:X}")
    raise AssertionError("cave did not terminate")


def assert_state(entry, *, rsoc, phase, stopped, expected_phase, expected_stopped):
    result = run_cave(
        entry,
        rsoc=rsoc,
        low=40,
        high=80,
        phase=phase,
        stopped=stopped,
    )
    assert result["phase"] is expected_phase
    assert result["stopped"] is expected_stopped
    assert result["low"] == 40
    assert result["high"] == 80


def test_first_plug_in_middle_band_holds_instead_of_topping_up():
    for entry in ("decision_entry", "preclear_entry"):
        assert_state(
            entry,
            rsoc=77,
            phase=False,
            stopped=False,
            expected_phase=False,
            expected_stopped=True,
        )
        assert_state(
            entry,
            rsoc=41,
            phase=False,
            stopped=True,
            expected_phase=False,
            expected_stopped=True,
        )


def test_low_started_cycle_survives_adapter_unplug_replug():
    # At/below low, firmware starts and records a charge cycle.
    for entry in ("decision_entry", "preclear_entry"):
        assert_state(
            entry,
            rsoc=40,
            phase=False,
            stopped=True,
            expected_phase=True,
            expected_stopped=False,
        )
        assert_state(
            entry,
            rsoc=30,
            phase=False,
            stopped=True,
            expected_phase=True,
            expected_stopped=False,
        )

        # In the middle band, phase=1 distinguishes interrupted charging from a
        # first plug, so even the D73A gate-fail entry keeps charging permitted.
        assert_state(
            entry,
            rsoc=60,
            phase=True,
            stopped=True,
            expected_phase=True,
            expected_stopped=False,
        )
        assert_state(
            entry,
            rsoc=77,
            phase=True,
            stopped=False,
            expected_phase=True,
            expected_stopped=False,
        )


def test_reaching_high_ends_cycle_and_hold_survives_replug():
    for entry in ("decision_entry", "preclear_entry"):
        assert_state(
            entry,
            rsoc=80,
            phase=True,
            stopped=False,
            expected_phase=False,
            expected_stopped=True,
        )
        assert_state(
            entry,
            rsoc=90,
            phase=True,
            stopped=False,
            expected_phase=False,
            expected_stopped=True,
        )
        assert_state(
            entry,
            rsoc=77,
            phase=False,
            stopped=True,
            expected_phase=False,
            expected_stopped=True,
        )


def test_full_state_sequence():
    # first plug at 77: hold
    state = run_cave("preclear_entry", rsoc=77, low=40, high=80, phase=False, stopped=False)
    assert (state["phase"], state["stopped"]) == (False, True)

    # discharge to low: begin cycle
    state = run_cave("decision_entry", rsoc=40, low=40, high=80, phase=state["phase"], stopped=state["stopped"])
    assert (state["phase"], state["stopped"]) == (True, False)

    # unplug/replug mid-cycle: continue
    state = run_cave("preclear_entry", rsoc=60, low=40, high=80, phase=state["phase"], stopped=state["stopped"])
    assert (state["phase"], state["stopped"]) == (True, False)

    # reach high: end cycle and hold
    state = run_cave("decision_entry", rsoc=80, low=40, high=80, phase=state["phase"], stopped=state["stopped"])
    assert (state["phase"], state["stopped"]) == (False, True)

    # replug after slight discharge: remain held
    state = run_cave("preclear_entry", rsoc=77, low=40, high=80, phase=state["phase"], stopped=state["stopped"])
    assert (state["phase"], state["stopped"]) == (False, True)


def test_invalid_or_disabled_pair_falls_back_to_distinct_stock_entries():
    # When low == 0, v2 is disabled and falls back strictly to stock behavior:
    # D729 stock behavior: below high clears stop; at/above high sets stop.
    below = run_cave("decision_entry", rsoc=77, low=0, high=80, phase=True, stopped=True)
    assert below["stopped"] is (77 >= 80)
    assert below["phase"] is True

    preclear = run_cave("preclear_entry", rsoc=77, low=0, high=80, phase=True, stopped=True)
    assert preclear["stopped"] is False
    assert preclear["phase"] is True

    # For invalid configurations (low >= 96 or high <= low), v2 executes
    # an override that unconditionally clears the stop bit and bypasses stock limit check.
    override_pairs = ((80, 80), (81, 80), (96, 99))
    for low, high in override_pairs:
        for rsoc in (20, 77, 99):
            for entry in ("decision_entry", "preclear_entry"):
                result = run_cave(entry, rsoc=rsoc, low=low, high=high, phase=True, stopped=True)
                assert result["stopped"] is False
                assert result["phase"] is True


def test_psw_contract_at_both_continuations():
    # D729's original SOC-high compare leaves carry set only below high.
    assert run_cave("decision_entry", rsoc=79, low=40, high=80, phase=True, stopped=False)["carry"] == 1
    assert run_cave("decision_entry", rsoc=80, low=40, high=80, phase=True, stopped=False)["carry"] == 0

    # D73A's original clear sequence leaves incoming carry untouched.
    assert run_cave(
        "preclear_entry", rsoc=77, low=40, high=80, phase=False, stopped=True, incoming_carry=0
    )["carry"] == 0
    assert run_cave(
        "preclear_entry", rsoc=77, low=40, high=80, phase=False, stopped=True, incoming_carry=1
    )["carry"] == 1
