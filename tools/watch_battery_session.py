#!/usr/bin/env python3
"""Read-only watcher for the EC battery-session debounce state."""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time

try:
    from ec.io import ec_read
except Exception as exc:  # pragma: no cover - hardware/project dependency
    print(
        "请在 mech-forza-control 项目中使用 sudo uv run 执行此脚本。",
        file=sys.stderr,
    )
    raise SystemExit(f"import error: {exc}")


ADDRESSES = {
    "0490": 0x0490,
    "0497": 0x0497,
    "05B9": 0x05B9,
    "05F1": 0x05F1,
    "05F2": 0x05F2,
    "05F3": 0x05F3,
    "05F4": 0x05F4,
    "05F5": 0x05F5,
    "0741": 0x0741,
}


def sample() -> dict[str, int]:
    values = {name: ec_read(address) for name, address in ADDRESSES.items()}
    target_lo = ec_read(0x0522)
    target_hi = ec_read(0x0523)
    values["target"] = (target_hi << 8) | target_lo
    return values


def main() -> None:
    parser = argparse.ArgumentParser(description="只读监听 EC 电池会话和去抖状态")
    parser.add_argument("-i", "--interval", type=float, default=0.05, help="采样间隔秒数")
    args = parser.parse_args()

    print("只读监听已启动。Ctrl-C 停止。", flush=True)
    previous = None
    previous_low_ms = 0
    bit1_low_since = None
    try:
        while True:
            now = time.monotonic()
            current = sample()
            bit1_low = (current["0490"] & 0x02) == 0
            if bit1_low:
                if bit1_low_since is None:
                    bit1_low_since = now
                low_ms = int((now - bit1_low_since) * 1000)
            else:
                bit1_low_since = None
                low_ms = 0

            changed = previous is None or current != previous
            crossed_clear_window = bit1_low and low_ms >= 720 and previous_low_ms < 720
            if changed or crossed_clear_window:
                stamp = dt.datetime.now().astimezone().isoformat(timespec="milliseconds")
                marker = " CLEAR-WINDOW>=720ms" if crossed_clear_window else ""
                print(
                    f"{stamp} 0490={current['0490']:02X} 0497={current['0497']:02X} "
                    f"F1-F5={current['05F1']:02X}/{current['05F2']:02X}/"
                    f"{current['05F3']:02X}/{current['05F4']:02X}/{current['05F5']:02X} "
                    f"0741={current['0741']:02X} 05B9={current['05B9']:02X} "
                    f"target={current['target']} bit1_low={low_ms}ms{marker}",
                    flush=True,
                )
            previous = current
            previous_low_ms = low_ms
            time.sleep(max(0.01, args.interval))
    except KeyboardInterrupt:
        print("\n监听结束。", flush=True)


if __name__ == "__main__":
    main()
