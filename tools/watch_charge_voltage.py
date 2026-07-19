#!/usr/bin/env python3
"""Read-only EC charge-voltage watcher for the AC reconnect experiment.

Run from the mech-forza-control uv project, for example:
  sudo uv run /home/texsd/Workdir/ec_reverse/tools/watch_charge_voltage.py
Stop with Ctrl-C.  The script never writes EC registers.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time

try:
    from ec.io import ec_read
except Exception as exc:  # pragma: no cover - hardware/project dependency
    print(
        "无法导入 ec.io。请在 mech-forza-control 项目中运行："
        " sudo uv run /home/texsd/Workdir/ec_reverse/tools/watch_charge_voltage.py",
        file=sys.stderr,
    )
    raise SystemExit(f"import error: {exc}")


def read_word(high: int, low: int) -> tuple[int, int, int]:
    """Return raw high/low bytes and the public little-endian word value."""
    hi = ec_read(high)
    lo = ec_read(low)
    return hi, lo, (lo << 8) | hi


def sample() -> dict[str, int]:
    base_hi, base_lo, base = read_word(0x030E, 0x030F)
    raw_hi, raw_lo, target = read_word(0x0522, 0x0523)
    cyc_hi, cyc_lo, cycles = read_word(0x04A6, 0x04A7)
    temp_hi, temp_lo, temp_raw = read_word(0x04A2, 0x04A3)
    return {
        "power": ec_read(0x0490),
        # 030E:030F is the telemetry cache's displayed big-endian word.
        "base": (base_hi << 8) | base_lo,
        "target": target,
        "base_raw": (base_hi << 8) | base_lo,
        "target_raw": (raw_hi << 8) | raw_lo,
        "cycles": cycles,
        "cycles_raw": (cyc_hi << 8) | cyc_lo,
        "temp_raw": temp_raw,
        "temp_hi": temp_hi,
        "temp_lo": temp_lo,
        "mode": ec_read(0x07A6),
        "current": ec_read(0x050B),
    }


def print_sample(s: dict[str, int], previous: dict[str, int] | None) -> None:
    now = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    power = s["power"]
    ac = "AC" if power & 1 else "BAT"
    changes = []
    if previous:
        for key, label in (("power", "0490"), ("target", "target"), ("base", "base"), ("mode", "07A6")):
            if s[key] != previous[key]:
                changes.append(f"{label}:{previous[key]:#x}->{s[key]:#x}")
    suffix = f"  CHANGE[{', '.join(changes)}]" if changes else ""
    print(
        f"{now} {ac} 0490=0x{power:02X} 030E/0F={s['base']}mV "
        f"0522/23={s['target']}mV (raw 0x{s['target_raw']:04X}) "
        f"07A6=0x{s['mode']:02X} 050B=0x{s['current']:02X} "
        f"cycles={s['cycles']} temp_raw=0x{s['temp_raw']:04X}{suffix}",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="持续只读监听 EC 充电电压目标")
    parser.add_argument("-i", "--interval", type=float, default=0.5, help="采样间隔，秒（默认 0.5）")
    args = parser.parse_args()
    if os.geteuid() != 0:
        print("提示：当前未以 root 运行，EC 访问可能失败；请使用 sudo uv run ...", file=sys.stderr)
    print("开始只读监听。现在可以插入 AC；不要拔插电池。Ctrl-C 停止。", flush=True)
    previous = None
    try:
        while True:
            try:
                current = sample()
                print_sample(current, previous)
                previous = current
            except Exception as exc:
                print(f"采样失败: {exc}", file=sys.stderr, flush=True)
            time.sleep(max(0.05, args.interval))
    except KeyboardInterrupt:
        print("\n监听结束。", flush=True)


if __name__ == "__main__":
    main()
