"""Configuration, file paths and app logging setup.

Everything tunable lives here. Values can be overridden at runtime by editing
``%LOCALAPPDATA%\\PeripheralBattery\\config.json`` -- in particular the HyperX
byte offsets confirmed with ``tools/hyperx_probe.py``.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
from dataclasses import dataclass, field

APP_NAME = "PeripheralBattery"


# --- Paths ------------------------------------------------------------------
def _local_appdata() -> str:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = os.path.expanduser("~")
    return base


APP_DIR = os.path.join(_local_appdata(), APP_NAME)
APP_LOG = os.path.join(APP_DIR, "app.log")
CONFIG_FILE = os.path.join(APP_DIR, "config.json")


def _razer_base() -> str:
    return os.path.join(_local_appdata(), "Razer")


# Log files that carry the device list + battery, newest-modified wins.
DEFAULT_RAZER_LOG_GLOBS = [
    # Synapse 4 (RazerAppEngine)
    os.path.join(_razer_base(), "RazerAppEngine", "User Data", "Logs",
                 "systray_systrayv*.log"),
    # Synapse 3
    os.path.join(_razer_base(), "Synapse3", "Log", "Razer Synapse 3.log"),
]


# --- Config sections --------------------------------------------------------
@dataclass
class RazerConfig:
    # Glob patterns, tried in order; the newest-matching file is used.
    log_globs: list[str] = field(default_factory=lambda: list(DEFAULT_RAZER_LOG_GLOBS))
    poll_interval: float = 5.0        # seconds between incremental log reads
    stale_seconds: float = 300.0      # no log writes for this long -> stale
    max_initial_read: int = 2_000_000  # cap the one-time startup read


@dataclass
class HyperXConfig:
    # --- device discovery -------------------------------------------------
    vendor_id: int = 0x03F0           # HP / HyperX
    usage_page: int = 0xFF13          # vendor-defined collection
    dongle_pid: int | None = 0x05B7   # None => accept any pid with usage_page
    charging_pid: int = 0x06B7        # present only while charging
    # --- protocol (confirmed on HyperX Cloud III Wireless) ---------------
    report_prefix: int = 0x66
    battery_command: int = 0x89
    packet_length: int = 52
    battery_offset: int = 4           # 0-based index into the response
    millivolt_offset: int = 2         # 0-based, 2 bytes big-endian
    max_valid_level: int = 100        # above this => headset off/unreachable
    # --- timing -----------------------------------------------------------
    poll_interval: float = 15.0
    read_timeout_ms: int = 500


@dataclass
class UIConfig:
    low_battery_threshold: int = 20
    start_minimized: bool = False
    close_to_tray: bool = True
    # --- desktop widget ---------------------------------------------------
    widget_enabled: bool = True          # park the mini card on the desktop
    widget_always_on_top: bool = True
    widget_visibility: str = "hide_fullscreen"   # always | hide_fullscreen | desktop_only
    widget_x: int | None = None          # None => bottom-right of the screen
    widget_y: int | None = None


@dataclass
class Config:
    razer: RazerConfig = field(default_factory=RazerConfig)
    hyperx: HyperXConfig = field(default_factory=HyperXConfig)
    ui: UIConfig = field(default_factory=UIConfig)


# --- Load / save ------------------------------------------------------------
def _apply(section_obj, data: dict) -> None:
    for key, value in (data or {}).items():
        if not hasattr(section_obj, key):
            continue
        # Accept "0x89" / "137" style numbers from hand-edited config.json.
        if isinstance(value, str):
            text = value.strip()
            for parse in (lambda t: int(t, 0), int):
                try:
                    value = parse(text)
                    break
                except (ValueError, TypeError):
                    continue
        setattr(section_obj, key, value)


def load() -> Config:
    """Load config, layering config.json over the defaults."""
    cfg = Config()
    if os.path.exists(CONFIG_FILE):
        try:
            # utf-8-sig: Notepad and PowerShell both like to prepend a BOM, and
            # plain utf-8 would then refuse to parse the file at all.
            with open(CONFIG_FILE, "r", encoding="utf-8-sig") as fh:
                data = json.load(fh)
            _apply(cfg.razer, data.get("razer", {}))
            _apply(cfg.hyperx, data.get("hyperx", {}))
            _apply(cfg.ui, data.get("ui", {}))
        except Exception:  # noqa: BLE001 - never let a bad config kill startup
            logging.getLogger(__name__).exception(
                "failed to read %s, using defaults", CONFIG_FILE)
    return cfg


def _as_dict(cfg: Config) -> dict:
    return {
        "razer": cfg.razer.__dict__,
        "hyperx": cfg.hyperx.__dict__,
        "ui": cfg.ui.__dict__,
    }


def save(cfg: Config) -> bool:
    """Write the whole config back.

    Used at runtime for things the UI changes itself -- the desktop widget's
    position and toggles. Written to a temporary file first, because a
    half-written config.json would be read as a broken one next start.
    """
    os.makedirs(APP_DIR, exist_ok=True)
    tmp = CONFIG_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(_as_dict(cfg), fh, indent=2, ensure_ascii=False)
        os.replace(tmp, CONFIG_FILE)
        return True
    except Exception:  # noqa: BLE001 - a failed save must never kill the app
        logging.getLogger(__name__).exception("could not write %s", CONFIG_FILE)
        return False


def save_default() -> None:
    """Write a default config.json (only if missing)."""
    if os.path.exists(CONFIG_FILE):
        return
    os.makedirs(APP_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
        json.dump(_as_dict(Config()), fh, indent=2, ensure_ascii=False)


# --- Logging ----------------------------------------------------------------
def setup_logging(debug: bool = False) -> logging.Logger:
    """Log to stdout and a rotating file under %LOCALAPPDATA%. Never raises."""
    os.makedirs(APP_DIR, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if not any(isinstance(h, logging.StreamHandler) and
               getattr(h, "_pb_console", False) for h in root.handlers):
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(fmt)
        console._pb_console = True  # type: ignore[attr-defined]
        root.addHandler(console)

    try:
        fileh = logging.handlers.RotatingFileHandler(
            APP_LOG, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        fileh.setFormatter(fmt)
        fileh._pb_file = True  # type: ignore[attr-defined]
        root.addHandler(fileh)
    except Exception:  # noqa: BLE001
        root.exception("could not open log file %s", APP_LOG)

    return logging.getLogger(APP_NAME)
