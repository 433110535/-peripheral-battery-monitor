"""Console smoke test for DeviceManager.

Runs both adapters in their own threads and prints every update, plus a
once-per-second heartbeat on the *main* thread. If the heartbeat keeps ticking
while updates arrive, polling cannot be blocking the caller.

Usage:
    python tools/check_manager.py            # run for 25 s
    python tools/check_manager.py -t 60      # run for 60 s
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time

# allow running as `python tools/check_manager.py` from the project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import load, setup_logging  # noqa: E402
from adapters.manager import DeviceManager  # noqa: E402
from models import DeviceStatus  # noqa: E402

START = time.monotonic()
_updates: list[tuple[float, DeviceStatus]] = []
_lock = threading.Lock()


def fmt(elapsed: float, status: DeviceStatus) -> str:
    battery = "--" if status.battery is None else f"{status.battery:>3}%"
    charge = {True: "充电中", False: "放电中", None: "  -  "}[status.charging]
    state = "在线" if status.online else "离线"
    extra = f"  error={status.error}" if status.error else ""
    return (f"[{elapsed:6.2f}s] {status.brand:<6} {state} {battery} {charge}"
            f"  name={status.name}{extra}")


def on_update(status: DeviceStatus) -> None:
    with _lock:
        _updates.append((time.monotonic() - START, status))
    print(fmt(time.monotonic() - START, status), flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-t", "--seconds", type=float, default=25.0)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    log = setup_logging(debug=args.verbose)
    cfg = load()
    print(f"poll intervals: Razer={cfg.razer.poll_interval}s  "
          f"HyperX={cfg.hyperx.poll_interval}s   running {args.seconds}s\n")

    manager = DeviceManager(cfg, log, on_update=on_update)
    manager.start()

    # Heartbeat on the main thread -- proves the main thread is not blocked.
    try:
        end = time.monotonic() + args.seconds
        while time.monotonic() < end:
            time.sleep(1.0)
            print(f"    heartbeat {time.monotonic() - START:5.1f}s "
                  f"(main thread alive)", flush=True)
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        manager.stop()

    # --- summary ----------------------------------------------------------
    print("\n=== summary ===")
    with _lock:
        updates = list(_updates)
    per_brand = {}
    for elapsed, status in updates:
        per_brand.setdefault(status.brand, []).append(elapsed)

    for brand, times in per_brand.items():
        gaps = [round(b - a, 2) for a, b in zip(times, times[1:])]
        print(f"{brand:<6} updates={len(times)} at {[round(t,1) for t in times]}")
        print(f"       gaps between updates: {gaps}")

    print("\nfinal snapshot:")
    for brand, status in manager.latest().items():
        print("  " + fmt(time.monotonic() - START, status))

    ok = len(per_brand) == 2 and all(len(v) >= 1 for v in per_brand.values())
    print(f"\nresult: {'OK' if ok else 'INCOMPLETE'} "
          f"(both adapters produced updates: {ok})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
