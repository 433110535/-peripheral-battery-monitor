"""HyperX HID probe / calibration tool.

Purpose
-------
HyperX Cloud III Wireless (and siblings) expose battery state over a
vendor-specific USB HID interface -- no NGENUITY required. This script talks to
that interface directly and dumps the *raw* response bytes so the layout can be
confirmed by hand and then pinned in ``config.py``.

Confirmed protocol (HyperX Cloud III Wireless, dongle PID 0x05B7)
----------------------------------------------------------------
  Request : 52 bytes, ``66 89`` then zero padding (no report-id prefix).
  Response: ``66 89 <mv_hi> <mv_lo> <level> 00 ...``
              byte 0      : 0x66  (echo)
              byte 1      : 0x89  (command echo)
              byte 2..3   : battery voltage, big-endian millivolts
              byte 4      : battery level 0-100
                            (>100 / 0xFF => headset off or out of range)
              bytes 5..61 : zero

  Charging is NOT in the payload: while the charge cable is connected the dongle
  also enumerates as a second USB device (PID 0x06B7). Its presence == charging.

Observed on a real unit:
  ``66 89 0E BC 2A 00 ..``  ->  3772 mV, 42 %

Usage
-----
    python tools/hyperx_probe.py            # one shot, all interfaces
    python tools/hyperx_probe.py --watch    # keep polling (Ctrl+C to stop)
    python tools/hyperx_probe.py --raw      # dump every report byte

Notes
-----
* VID 0x03F0 is HP/HyperX. Nothing is hardcoded by PID: every HID interface for
  that vendor is enumerated, then the vendor usage page (0xFF13) is preferred.
* Read-only in effect: the only thing written is the battery query.
"""

from __future__ import annotations

import argparse
import sys
import time

try:
    import hid
except ImportError:  # pragma: no cover - depends on local env
    print("ERROR: the 'hidapi' package is not installed.")
    print("       Install it with:  pip install hidapi")
    raise SystemExit(1)


# --- Protocol constants (confirmed against a real Cloud III Wireless) -------
DEFAULT_VID = 0x03F0
REPORT_PREFIX = 0x66        # first payload byte
CMD_BATTERY = 0x89          # battery query command
PACKET_LEN = 52             # documented request length (62 also works)

BATTERY_OFFSET = 4          # 0-based: level, 0-100
MILLIVOLT_OFFSET = 2        # 0-based: 2-byte big-endian millivolts

VENDOR_USAGE_PAGE = 0xFF13  # what the Cloud III Wireless dongle reports
CHARGING_PID = 0x06B7       # second USB device present only while charging

# A level above this means "no reading" (headset off / out of range).
MAX_VALID_LEVEL = 100


def hexdump(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def hexdump_indexed(data: bytes) -> str:
    lines = []
    for i in range(0, len(data), 16):
        chunk = data[i : i + 16]
        lines.append(f"  [{i:3d}] " + " ".join(f"{b:02X}" for b in chunk))
    return "\n".join(lines)


def describe(path: bytes) -> str:
    try:
        return path.decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return repr(path)


def enumerate_hid(vid: int, pid: int = 0) -> list[dict]:
    try:
        return list(hid.enumerate(vid, pid))
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: hid.enumerate failed: {exc}")
        return []


def show_all_interfaces(vid: int) -> list[dict]:
    devices = enumerate_hid(vid, 0)
    print(f"\nAll VID 0x{vid:04X} HID interfaces: {len(devices)}")
    print("-" * 78)
    for d in devices:
        print(f"  PID={d['product_id']:#06x} "
              f"usage_page={d['usage_page']:#06x} usage={d['usage']:#06x} "
              f"iface={d.get('interface_number', -1)}")
        print(f"      product={d.get('product_string')!r} "
              f"serial={d.get('serial_number')!r}")
        print(f"      path={describe(d['path'])}")
    print("-" * 78)
    return devices


def charging_present(vid: int, pid: int) -> bool:
    return len(enumerate_hid(vid, pid)) > 0


def build_request(command: int, length: int) -> bytes:
    return bytes([REPORT_PREFIX, command]) + bytes(length - 2)


def open_device(path: bytes):
    dev = hid.device()
    dev.open_path(path)
    # NOTE: do NOT call set_nonblocking() -- Windows hidapi does not support it.
    return dev


def drain(dev) -> None:
    """Throw away any queued reports (with a hard cap so we can't spin).

    NOTE: a timeout of 0 blocks on this binding, so a tiny positive timeout is
    used instead.
    """
    for _ in range(32):
        try:
            if not dev.read(64, 1):
                break
        except Exception:  # noqa: BLE001
            break


def query(dev, command: int, length: int, timeout_ms: int,
          read_size: int, raw: bool) -> bytes | None:
    """Send one command, return the first echo-matching report (or None)."""
    drain(dev)
    req = build_request(command, length)
    try:
        sent = dev.write(req)
    except Exception as exc:  # noqa: BLE001
        print(f"    write FAILED: {exc}")
        return None
    print(f"    -> write {len(req)}B (sent={sent}): {hexdump(req[:8])} ...")

    deadline = time.time() + timeout_ms / 1000.0
    match: bytes | None = None
    while time.time() < deadline:
        try:
            data = dev.read(read_size, timeout_ms)
        except Exception as exc:  # noqa: BLE001
            print(f"    read FAILED: {exc}")
            break
        if not data:
            break
        report = bytes(data)
        if raw:
            print(f"    <- report {len(report)}B: {hexdump(report)}")
        # Correlate: report id echo + command echo.
        if len(report) >= 2 and report[0] == REPORT_PREFIX and report[1] == command:
            match = report
            break
    return match


def interpret(report: bytes) -> None:
    if len(report) <= BATTERY_OFFSET:
        print("    (report too short to interpret)")
        return
    mv = (report[MILLIVOLT_OFFSET] << 8) | report[MILLIVOLT_OFFSET + 1]
    level = report[BATTERY_OFFSET]
    print(f"\n    full response: {hexdump(report)}")
    print(hexdump_indexed(report))
    print(f"\n    byte 2..3 (millivolts) : {mv} mV")
    print(f"    byte 4    (level)      : {level}"
          + ("" if level <= MAX_VALID_LEVEL else "  <-- out of range: headset off/unreachable"))
    if level <= MAX_VALID_LEVEL:
        print(f"    => battery = {level}%")


def probe_interface(d: dict, args: argparse.Namespace) -> None:
    print(f"\n=== Interface PID {d['product_id']:#06x} "
          f"usage_page={d['usage_page']:#06x} ===")
    try:
        dev = open_device(d["path"])
    except Exception as exc:  # noqa: BLE001
        print(f"    Could not open: {exc}")
        return
    try:
        report = query(dev, args.cmd, args.packet_len, args.timeout,
                       args.read_size, args.raw)
        if report is None:
            print("    no matching response")
            return
        interpret(report)

        if args.charge_cmd is not None:
            print(f"\n    [charge query cmd={args.charge_cmd:#04x}]")
            r2 = query(dev, args.charge_cmd, args.packet_len, args.timeout,
                       args.read_size, args.raw)
            print("    no matching response" if r2 is None
                  else f"    response: {hexdump(r2)}")
    finally:
        try:
            dev.close()
        except Exception:
            pass


def is_vendor_iface(d: dict) -> bool:
    return d["usage_page"] >= 0xFF00


def one_pass(args: argparse.Namespace) -> None:
    devices = show_all_interfaces(args.vid)
    if not devices:
        print("\nNo HyperX HID interfaces found.")
        print("  - Is the wireless dongle plugged in?")
        print("  - Is the headset powered on?")
        return

    charging = charging_present(args.vid, CHARGING_PID)
    print(f"Charging device (PID {CHARGING_PID:#06x}) present: "
          f"{'YES -> charging' if charging else 'no'}")

    # Only the vendor-defined interface answers the battery query. Talking to the
    # standard audio/HID collections can block or is simply irrelevant, so skip
    # them unless --all was given.
    targets = devices if args.all else [d for d in devices if is_vendor_iface(d)]
    if not targets:
        print("No vendor (0xFFxx) interface found; re-run with --all to probe "
              "every interface.")
        return
    ordered = sorted(targets, key=lambda x: x["usage_page"] != VENDOR_USAGE_PAGE)
    for d in ordered:
        probe_interface(d, args)


def main() -> int:
    ap = argparse.ArgumentParser(description="HyperX HID battery probe")
    ap.add_argument("--vid", type=lambda x: int(x, 0), default=DEFAULT_VID)
    ap.add_argument("--cmd", type=lambda x: int(x, 0), default=CMD_BATTERY)
    ap.add_argument("--charge-cmd", type=lambda x: int(x, 0), default=None,
                    help="optional charge query command; default none")
    ap.add_argument("--packet-len", type=int, default=PACKET_LEN)
    ap.add_argument("--read-size", type=int, default=64)
    ap.add_argument("--timeout", type=int, default=500)
    ap.add_argument("--raw", action="store_true",
                    help="print every report received")
    ap.add_argument("--all", action="store_true",
                    help="probe every interface, not just the 0xFFxx vendor one")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--interval", type=float, default=2.0)
    args = ap.parse_args()

    print("=" * 78)
    print("HyperX HID battery probe")
    print(f"  vid={args.vid:#06x} cmd={args.cmd:#04x} packet_len={args.packet_len}")
    print("=" * 78)

    if not args.watch:
        one_pass(args)
        print("\nDone.\n")
        return 0

    print(f"Watching every {args.interval}s (Ctrl+C to stop) ...")
    try:
        while True:
            one_pass(args)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
