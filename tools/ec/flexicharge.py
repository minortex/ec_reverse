#!/usr/bin/env python3
"""Configure and inspect the EC FlexiCharge v2/v2.2 hysteresis state.

The v2 firmware uses EC[0x07D0].bit7 as a volatile ``cycle-active`` phase
latch.  This lets it distinguish a first adapter insertion in the middle of
the low/high window from a charge cycle that started at/below the lower limit
and was interrupted by unplugging the adapter.
"""

import argparse

from i2ec_rw import (
    DEFAULT_BASE_PORT,
    WRITE_CONFIRMATION,
    format_byte,
    open_i2ec,
    parse_integer,
)

HIGH_LIMIT = 0x07B9
LOW_LIMIT = 0x07D0
RSOC = 0x04AB
SPCTRL1 = 0x200D
LOW_SHADOW = 0x0773
SAVE_MAGIC_0 = 0x077E
SAVE_MAGIC_1 = 0x077F


def require_write_unlock(args, i2ec):
    if not args.allow_write or args.confirm != WRITE_CONFIRMATION:
        raise RuntimeError(
            "I2EC write is locked; pass --allow-write and "
            f"--confirm {WRITE_CONFIRMATION} only with the intended RW firmware"
        )
    control = i2ec.read(SPCTRL1)
    if control & 0x03 != 0x03:
        raise RuntimeError(
            f"EC does not report I2EC read-write mode (SPCTRL1={format_byte(control)})"
        )


def read_state(i2ec):
    high_raw = i2ec.read(HIGH_LIMIT)
    low_raw = i2ec.read(LOW_LIMIT)
    return {
        "rsoc": i2ec.read(RSOC),
        "high_raw": high_raw,
        "high": high_raw & 0x7F,
        "stopped": bool(high_raw & 0x80),
        "low_raw": low_raw,
        "low": low_raw & 0x7F,
        "cycle_active": bool(low_raw & 0x80),
        "low_shadow": i2ec.read(LOW_SHADOW),
        "save_magic_0": i2ec.read(SAVE_MAGIC_0),
        "save_magic_1": i2ec.read(SAVE_MAGIC_1),
    }


def print_state(state):
    print(f"RSOC:             {state['rsoc']}%")
    high_text = "100%/unrestricted" if state["high"] == 0 else f"{state['high']}%"
    print(
        f"upper 0x07B9:     {format_byte(state['high_raw'])} "
        f"(limit={high_text}, stop-bit={'set' if state['stopped'] else 'clear'})"
    )
    print(
        f"lower 0x07D0:     {format_byte(state['low_raw'])} "
        f"(limit={state['low']}%, cycle-active={'yes' if state['cycle_active'] else 'no'})"
    )
    shadow_valid = bool(state["low_shadow"] & 0x80)
    shadow_low = state["low_shadow"] & 0x7F
    print(
        f"saved low 0x0773: {format_byte(state['low_shadow'])} "
        f"({'valid, low=' + str(shadow_low) + '%' if shadow_valid else 'no v2.2 record'})"
    )
    handshake = (state["save_magic_0"], state["save_magic_1"])
    if handshake == (0xA5, 0x78):
        save_text = "commit requested/pending"
    elif handshake == (0x55, 0xAA):
        save_text = "stock block marker/commit consumed"
    else:
        save_text = "idle or platform-owned state"
    print(
        f"save handshake:    {format_byte(handshake[0])} {format_byte(handshake[1])} "
        f"({save_text})"
    )
    if state["low"] == 0 and state["high"] == 0:
        print("mode:              disabled/unrestricted 0-100; FlexiCharge logic is bypassed")
    elif state["low"] == 0:
        print("mode:              official upper-only compatibility; lower hysteresis disabled")
    elif state["high"] == 0:
        print("mode:              disabled/unrestricted; firmware clears runtime stop and phase bits")
    elif not 1 <= state["low"] <= 95 or not state["low"] < state["high"]:
        print("mode:              invalid pair; firmware treats it as disabled/unrestricted")
    elif state["rsoc"] <= state["low"]:
        print("mode:              lower reached: v2 should start/continue a charge cycle")
    elif state["rsoc"] >= state["high"]:
        print("mode:              upper reached: v2 should end the cycle and inhibit charging")
    elif state["cycle_active"]:
        print("mode:              middle band: low-started cycle remains active across adapter replug")
    else:
        print("mode:              middle band hold: charging remains inhibited until RSOC <= lower")

    if state["high"] != 0 and 1 <= state["low"] <= 95 and state["low"] < state["high"]:
        expected_stopped = not state["cycle_active"]
        if state["rsoc"] <= state["low"]:
            expected_stopped = False
        elif state["rsoc"] >= state["high"]:
            expected_stopped = True
        if state["stopped"] != expected_stopped:
            print("warning:           stop-bit has not yet converged to the v2 phase decision")


def command_status(args):
    with open_i2ec(args.base_port, args.device) as i2ec:
        state = read_state(i2ec)
    print_state(state)


def choose_cycle_active(old, low, high, resume_cycle=False):
    """Choose the phase written with a new window without inventing history."""
    if old["rsoc"] <= low:
        return True
    if old["rsoc"] >= high:
        return False
    if resume_cycle:
        return True
    same_window = old["low"] == low and old["high"] == high
    return same_window and old["cycle_active"]


def command_set(args):
    if not 1 <= args.low <= 95:
        raise ValueError("lower threshold must be between 1 and 95")
    if not 2 <= args.high <= 99:
        raise ValueError("upper threshold must be between 2 and 99 for hysteresis mode")
    if args.low >= args.high:
        raise ValueError("lower threshold must be less than upper threshold")

    with open_i2ec(args.base_port, args.device) as i2ec:
        require_write_unlock(args, i2ec)
        old = read_state(i2ec)
        active = choose_cycle_active(
            old,
            args.low,
            args.high,
            resume_cycle=args.resume_cycle,
        )
        low_raw = args.low | (0x80 if active else 0x00)
        high_raw = args.high | (0x00 if active else 0x80)
        # Write lower first so a currently asserted stop bit never observes a
        # new upper threshold paired with an unintended stale lower threshold.
        i2ec.write(LOW_LIMIT, low_raw)
        i2ec.write(HIGH_LIMIT, high_raw)
        new = read_state(i2ec)

    print(f"configured FlexiCharge window: {args.low}% -> {args.high}%")
    print_state(new)
    if active:
        print("phase initialization: active low-started charge cycle")
    else:
        print("phase initialization: hold in the middle band until RSOC reaches the lower limit")
    print(
        "note: v2.2 commits the lower threshold asynchronously; cycle-active "
        "remains volatile and is reconstructed from SOC after a true EC reset"
    )


def command_disable(args):
    with open_i2ec(args.base_port, args.device) as i2ec:
        require_write_unlock(args, i2ec)
        old = read_state(i2ec)
        i2ec.write(LOW_LIMIT, 0x00)
        # GCU encodes an unrestricted 100% upper setting as low seven bits 0;
        # clear the policy stop bit too so disabling cannot leave charging held.
        i2ec.write(HIGH_LIMIT, 0x00)
        new = read_state(i2ec)

    print("FlexiCharge disabled; restored canonical 0-100 unrestricted state")
    print_state(new)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-port", type=parse_integer, default=DEFAULT_BASE_PORT)
    parser.add_argument("--device", default="/dev/port")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="read current RSOC and threshold state")
    status.set_defaults(func=command_status)

    set_parser = subparsers.add_parser("set", help="configure lower/upper hysteresis thresholds")
    set_parser.add_argument("--low", type=parse_integer, required=True)
    set_parser.add_argument("--high", type=parse_integer, required=True)
    set_parser.add_argument(
        "--resume-cycle",
        action="store_true",
        help=(
            "mark a middle-band state as an already active low-started cycle; "
            "use only when intentionally resuming an interrupted cycle"
        ),
    )
    set_parser.add_argument("--allow-write", action="store_true")
    set_parser.add_argument("--confirm")
    set_parser.set_defaults(func=command_set)

    disable = subparsers.add_parser("disable", help="disable hysteresis and allow stock unrestricted charging")
    disable.add_argument("--allow-write", action="store_true")
    disable.add_argument("--confirm")
    disable.set_defaults(func=command_disable)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
