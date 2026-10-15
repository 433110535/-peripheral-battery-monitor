"""RazerAdapter -- read battery straight out of Razer Synapse's log files.

No USB reverse engineering: Synapse writes the full device list (including
``powerStatus`` with the battery level) to a plain-text log, so we just tail
that file and parse the JSON it contains.

Confirmed on Synapse 4 (2026, Razer Viper V3 Pro):

    [ts] info: [getConnectingDevices] ~ file: Synapse.js:55 ~
        connectingDeviceData: [{"serialNumber":"...","productName":{"en":"Razer Viper V3 Pro White Edition"},"powerStatus":{"chargingStatus":"NoCharge_BatteryFull","level":80}, ...}]

``chargingStatus`` observed values:

    "Charging"              -> on the cable
    "NoCharge_BatteryFull"  -> on battery (name is a Razer quirk; it also shows
                               at 70-80%, so it does NOT mean 100%)
    "off"                   -> mouse switched off / asleep

Watchdog watches the log directory so a new line is picked up immediately; the
5 s poll in DeviceManager is a safety net. Reads are incremental (byte offset),
so the cost per poll is tiny.
"""

from __future__ import annotations

import glob
import json
import os
import re
import threading
import time

from adapters.base import BaseAdapter
from models import DeviceStatus

try:  # watchdog is optional at runtime; polling still works without it
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    _HAVE_WATCHDOG = True
except Exception:  # noqa: BLE001
    _HAVE_WATCHDOG = False


# Loose fallback patterns, used only when the JSON can't be parsed.
_FALLBACK_BATTERY_RE = re.compile(r"(?i)battery[^\d]{0,20}(\d{1,3})\s*%?")
_FALLBACK_LEVEL_RE = re.compile(r'"level"\s*:\s*(\d{1,3})')

_OFF_VALUES = {"off", "0"}


def _extract_json(line: str):
    """Return the JSON object/array logged after the '~' separator, or None."""
    segment = line.rsplit("~", 1)[-1]
    for match in re.finditer(r"[\[{]", segment):
        try:
            return json.loads(segment[match.start():].strip())
        except Exception:  # noqa: BLE001
            continue
    return None


def _device_name(device: dict) -> str | None:
    for key in ("productName", "name"):
        value = device.get(key)
        if isinstance(value, dict):
            if "en" in value:
                return value["en"]
            if value:
                return next(iter(value.values()))
        elif isinstance(value, str):
            return value
    return None


def parse_devices(text: str):
    """Yield ``(name, level, charging_status)`` for each device with a
    ``powerStatus`` block found in ``text``."""
    for line in text.splitlines():
        if '"powerStatus"' not in line:
            continue
        obj = _extract_json(line)
        if obj is None:
            continue
        devices = obj.get("devices", obj) if isinstance(obj, dict) else obj
        if not isinstance(devices, list):
            continue
        for device in devices:
            if not isinstance(device, dict):
                continue
            power = device.get("powerStatus")
            if not isinstance(power, dict):
                continue
            yield _device_name(device), power.get("level"), power.get("chargingStatus")


if _HAVE_WATCHDOG:

    class _LogEventHandler(FileSystemEventHandler):
        """Wake the adapter when one of the watched log files changes."""

        def __init__(self, adapter: "RazerAdapter") -> None:
            self._adapter = adapter
            super().__init__()

        def on_any_event(self, event):  # noqa: D102
            try:
                name = os.path.basename(str(getattr(event, "src_path", "")))
                if any(pattern.match(name) for pattern in self._adapter._watchers):
                    self._adapter.wake()
            except Exception:  # noqa: BLE001
                pass

else:  # pragma: no cover - watchdog is a declared dependency

    class _LogEventHandler:  # type: ignore[no-redef]
        def __init__(self, adapter: "RazerAdapter") -> None:
            self._adapter = adapter


class RazerAdapter(BaseAdapter):
    brand = "Razer"
    display_name = "雷蛇鼠标"

    def __init__(self, config, logger=None) -> None:
        super().__init__(config, logger)
        self._razer = config.razer
        self._lock = threading.RLock()

        self._path: str | None = None
        self._offset = 0
        self._partial = b""

        self._name: str | None = None
        self._level: int | None = None
        self._charging_status: str | None = None
        self._last_parse_ts: float | None = None

        self._observer = None
        # Compiled basename matchers for the watchdog handler.
        self._watchers = [re.compile(fnmatch_to_regex(os.path.basename(g)))
                          for g in self._razer.log_globs]

    # --- BaseAdapter ------------------------------------------------------
    @property
    def interval(self) -> float:
        return self._razer.poll_interval

    def start(self) -> None:
        if not _HAVE_WATCHDOG:
            self.log.info("watchdog not available; using polling only")
            return
        dirs = {_glob_dir(g) for g in self._razer.log_globs}
        dirs = {d for d in dirs if d and os.path.isdir(d)}
        if not dirs:
            self.log.info("no Razer log directory found yet; polling only")
            return
        try:
            observer = Observer()
            handler = _LogEventHandler(self)
            for directory in dirs:
                observer.schedule(handler, directory, recursive=False)
            observer.daemon = True
            observer.start()
            self._observer = observer
            self.log.info("watching Razer logs in: %s", ", ".join(sorted(dirs)))
        except Exception:  # noqa: BLE001
            self.log.exception("could not start watchdog observer")

    def stop(self) -> None:
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=2)
            except Exception:  # noqa: BLE001
                self.log.exception("error stopping watchdog observer")
            finally:
                self._observer = None

    def wake(self) -> None:
        self.change_event.set()

    def read(self) -> DeviceStatus:
        with self._lock:
            path = self._resolve_path()
            if path is None:
                return self._error(
                    "未找到 Synapse 日志文件，请确认 Razer Synapse 已安装并运行")
            if path != self._path:
                self._load_full(path)
            else:
                self._read_delta(path)
            return self._build_status(path)

    # --- file handling ----------------------------------------------------
    def _resolve_path(self) -> str | None:
        best, best_mtime = None, -1.0
        for pattern in self._razer.log_globs:
            for candidate in glob.glob(pattern):
                try:
                    mtime = os.path.getmtime(candidate)
                except OSError:
                    continue
                if mtime > best_mtime:
                    best, best_mtime = candidate, mtime
        return best

    def _load_full(self, path: str) -> None:
        """Read (the tail of) the file once and reset the byte offset."""
        try:
            size = os.path.getsize(path)
        except OSError:
            return
        start = max(0, size - self._razer.max_initial_read)
        try:
            with open(path, "rb") as fh:
                fh.seek(start)
                data = fh.read()
        except OSError as exc:
            self.log.warning("cannot read %s: %s", path, exc)
            return
        if start > 0:
            nl = data.find(b"\n")
            data = data[nl + 1:] if nl >= 0 else b""
        self._apply(data.decode("utf-8", "replace"))
        self._path = path
        self._offset = size
        self._partial = b""
        self.log.debug("loaded %s (%d bytes from offset %d)", path, size, start)

    def _read_delta(self, path: str) -> None:
        try:
            size = os.path.getsize(path)
        except OSError:
            return
        if size < self._offset:  # truncated or replaced (rotation)
            self.log.info("log shrank (%d -> %d); reloading", self._offset, size)
            self._load_full(path)
            return
        if size == self._offset:
            return
        try:
            with open(path, "rb") as fh:
                fh.seek(self._offset)
                data = self._partial + fh.read()
        except OSError as exc:
            self.log.warning("cannot read delta of %s: %s", path, exc)
            return
        self._offset = size
        end = data.rfind(b"\n")
        if end < 0:  # half a line so far, keep it for next time
            self._partial = data
            return
        self._partial = data[end + 1:]
        self._apply(data[: end + 1].decode("utf-8", "replace"))

    def _apply(self, text: str) -> None:
        found = None
        for name, level, charging in parse_devices(text):
            if level is None and charging is None:
                continue
            found = (name, level, charging)
        if found is None:
            self._apply_fallback(text)
            return
        name, level, charging = found
        self._name = name or self._name
        self._level = _coerce_level(level)
        self._charging_status = charging
        self._last_parse_ts = time.time()

    def _apply_fallback(self, text: str) -> None:
        """Last resort: loose regex over any freshly read text."""
        match = _FALLBACK_LEVEL_RE.search(text) or _FALLBACK_BATTERY_RE.search(text)
        if match:
            self._level = _coerce_level(int(match.group(1)))
            self._last_parse_ts = time.time()

    # --- status -----------------------------------------------------------
    def _build_status(self, path: str) -> DeviceStatus:
        if self._last_parse_ts is None and self._level is None:
            return self._error(
                "Synapse 日志中暂未解析到电量，请确认 Synapse 已识别到鼠标")

        try:
            age = time.time() - os.path.getmtime(path)
        except OSError:
            age = 0.0

        online = self._charging_status not in _OFF_VALUES
        status = DeviceStatus(
            name=self._name or self.display_name,
            brand=self.brand,
            battery=self._level,
            charging=_charging_flag(self._charging_status),
            online=online,
            last_update=time.time(),
            error=None,
        )
        if not online:
            # Mouse switched off / asleep. Synapse keeps reporting its last known
            # level, so keep the number and just flag the device as inactive.
            status.charging = None
            status.error = "鼠标已关闭或处于休眠"
        elif age > self._razer.stale_seconds:
            # Synapse stops writing while the device is idle, so an old log does
            # NOT mean Synapse is down. Keep showing the last value, but mark it
            # as not fresh so the UI can grey it out.
            status.online = False
            status.error = f"日志已 {_human_age(age)}无更新，数值可能已过期"
        return status


# --- small helpers ----------------------------------------------------------
def _human_age(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 90:
        return f"{seconds} 秒"
    if seconds < 5400:
        return f"{seconds // 60} 分钟"
    return f"{seconds // 3600} 小时"


def _coerce_level(value) -> int | None:
    try:
        level = int(value)
    except (TypeError, ValueError):
        return None
    if 0 <= level <= 100:
        return level
    return None


def _charging_flag(status) -> bool | None:
    if status is None:
        return None
    text = str(status).strip().lower()
    if text in _OFF_VALUES or text == "":
        return None
    if text.startswith("charging"):
        return True
    return False


def fnmatch_to_regex(pattern: str) -> str:
    """Translate a simple ``*``/``?`` glob into an anchored regex string."""
    out = []
    for ch in pattern:
        if ch == "*":
            out.append(".*")
        elif ch == "?":
            out.append(".")
        else:
            out.append(re.escape(ch))
    return "^" + "".join(out) + "$"


def _glob_dir(pattern: str) -> str:
    """Directory part of a glob, i.e. everything before the first wildcard."""
    parts = pattern.split(os.sep)
    root = []
    for part in parts:
        if any(ch in part for ch in "*?["):
            break
        root.append(part)
    return os.sep.join(root)
