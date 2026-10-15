"""HyperXAdapter -- talk to the HyperX Cloud III Wireless dongle over raw HID.

No NGENUITY: the dongle answers a small vendor-defined HID protocol directly.

Protocol (confirmed on HyperX Cloud III Wireless, dongle PID 0x05B7,
usage page 0xFF13 -- see tools/hyperx_probe.py):

    request  : 52 bytes, ``66 89`` followed by zeros
               (no report-id prefix on the write; hidapi pads to the 62-byte
                report length for us)
    response : ``66 89 <mv_hi> <mv_lo> <level> 00 ...``
                  [0] 0x66        report echo
                  [1] 0x89        command echo
                  [2..3]          battery voltage, big-endian millivolts
                  [4]             battery level 0-100
                  [5..]           zero

    A level above 100 (typically 0xFF) means the headset is off / out of range.

    Charging is NOT in the payload. While the charge cable is connected the
    headset enumerates as a second USB device (PID 0x06B7); its presence *is*
    the charging signal.

All byte offsets / ids come from ``config.HyperXConfig`` so they can be retuned
without touching this file.
"""

from __future__ import annotations

import threading
import time

from adapters.base import BaseAdapter
from models import DeviceStatus

try:
    import hid
except ImportError:  # pragma: no cover
    hid = None  # type: ignore[assignment]


class HyperXAdapter(BaseAdapter):
    brand = "HyperX"
    display_name = "HyperX 耳机"

    def __init__(self, config, logger=None) -> None:
        super().__init__(config, logger)
        self._cfg = config.hyperx
        self._lock = threading.RLock()
        self._dev = None
        self._path: bytes | None = None

    # --- BaseAdapter ------------------------------------------------------
    @property
    def interval(self) -> float:
        return self._cfg.poll_interval

    def stop(self) -> None:
        self._close()

    def read(self) -> DeviceStatus:
        if hid is None:
            return self._error("未安装 hidapi，无法访问 HyperX 接收器")

        with self._lock:
            charging = self._charging_present()
            try:
                report = self._query_battery()
            except _DongleMissing as exc:
                self._close()
                return self._error(str(exc))
            except Exception as exc:  # noqa: BLE001
                # Bad handle (unplugged mid-read) -> drop it and retry next poll.
                self._close()
                self.log.warning("HyperX HID query failed: %s", exc)
                return self._error(f"读取 HyperX 失败：{exc}")

        if report is None:
            return self._error("HyperX 接收器无响应")

        level = self._extract_level(report)
        millivolts = self._extract_millivolts(report)
        self.log.debug("HyperX raw=%s level=%s mv=%s charging=%s",
                       " ".join(f"{b:02X}" for b in report),
                       level, millivolts, charging)

        if level is None or level > self._cfg.max_valid_level:
            return DeviceStatus(
                name=self.display_name, brand=self.brand, battery=None,
                charging=None, online=False, last_update=time.time(),
                error="耳机未开机或超出范围",
            )

        return DeviceStatus(
            name=self.display_name,
            brand=self.brand,
            battery=level,
            charging=charging,
            online=True,
            last_update=time.time(),
            error=None,
        )

    # --- device handling --------------------------------------------------
    def _find_dongle_path(self) -> bytes | None:
        if hid is None:
            return None
        candidates = hid.enumerate(self._cfg.vendor_id, 0)
        preferred: bytes | None = None
        for d in candidates:
            if self._cfg.dongle_pid is not None and \
                    d["product_id"] != self._cfg.dongle_pid:
                continue
            if d["usage_page"] < 0xFF00:
                continue  # skip the standard audio collections
            if self._cfg.usage_page and d["usage_page"] == self._cfg.usage_page:
                return d["path"]
            preferred = preferred or d["path"]
        return preferred

    def _ensure_open(self):
        if self._dev is not None:
            return
        path = self._find_dongle_path()
        if path is None:
            raise _DongleMissing(
                "未找到 HyperX 接收器，请确认接收器已插入且耳机已开机")
        dev = hid.device()
        dev.open_path(path)
        self._dev = dev
        self._path = path
        self.log.info("opened HyperX dongle at %s", path.decode("ascii", "replace"))

    def _close(self) -> None:
        if self._dev is not None:
            try:
                self._dev.close()
            except Exception:  # noqa: BLE001
                pass
            finally:
                self._dev = None
                self._path = None

    def _drain(self) -> None:
        # timeout 0 blocks on this hidapi build, so use a tiny positive one.
        for _ in range(16):
            try:
                if not self._dev.read(64, 1):
                    break
            except Exception:  # noqa: BLE001
                break

    def _build_request(self) -> bytes:
        length = self._cfg.packet_length
        head = bytes([self._cfg.report_prefix, self._cfg.battery_command])
        return head + bytes(max(0, length - len(head)))

    def _query_battery(self) -> bytes | None:
        self._ensure_open()
        self._drain()
        request = self._build_request()
        self._dev.write(request)

        deadline = time.time() + self._cfg.read_timeout_ms / 1000.0
        while time.time() < deadline:
            data = self._dev.read(64, self._cfg.read_timeout_ms)
            if not data:
                break
            report = bytes(data)
            if len(report) >= 2 and \
                    report[0] == self._cfg.report_prefix and \
                    report[1] == self._cfg.battery_command:
                return report
        return None

    def _charging_present(self) -> bool:
        if hid is None or not self._cfg.charging_pid:
            return False
        try:
            return len(hid.enumerate(self._cfg.vendor_id,
                                     self._cfg.charging_pid)) > 0
        except Exception:  # noqa: BLE001
            return False

    # --- parsing ----------------------------------------------------------
    def _extract_level(self, report: bytes) -> int | None:
        offset = self._cfg.battery_offset
        if offset < 0 or offset >= len(report):
            return None
        return report[offset]

    def _extract_millivolts(self, report: bytes) -> int | None:
        offset = self._cfg.millivolt_offset
        if offset < 0 or offset + 1 >= len(report):
            return None
        return (report[offset] << 8) | report[offset + 1]


class _DongleMissing(Exception):
    """Raised when no HyperX vendor HID interface can be found."""
