"""Adapter base class.

Each brand gets its own adapter and they must not affect one another: a failure
inside one adapter only ever produces a `DeviceStatus` with ``online=False`` and
an ``error`` string, never an exception that escapes.
"""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod

from models import DeviceStatus


class BaseAdapter(ABC):
    brand: str = "Unknown"
    display_name: str = "设备"

    def __init__(self, config, logger: logging.Logger | None = None) -> None:
        self.config = config
        self.log = logger or logging.getLogger(type(self).__name__)
        # Set by adapters that can detect a source change (e.g. watchdog), so the
        # DeviceManager can poll sooner than the fixed interval.
        self.change_event = threading.Event()

    # --- to implement -----------------------------------------------------
    @property
    @abstractmethod
    def interval(self) -> float:
        """Seconds between polls."""

    @abstractmethod
    def read(self) -> DeviceStatus:
        """Do one poll and return the current status. May raise."""

    # --- optional hooks ---------------------------------------------------
    def start(self) -> None:
        """Called once before the polling loop starts (e.g. start watchdog)."""

    def stop(self) -> None:
        """Called once when shutting down."""

    def wake(self) -> None:
        """Signal the manager that the source changed and it should poll now."""
        self.change_event.set()

    # --- safe wrapper -----------------------------------------------------
    def safe_read(self) -> DeviceStatus:
        try:
            status = self.read()
            status.brand = self.brand
            if not status.name:
                status.name = self.display_name
            return status
        except Exception as exc:  # noqa: BLE001 - isolate every adapter
            self.log.exception("%s adapter read failed", self.brand)
            return DeviceStatus(
                name=self.display_name,
                brand=self.brand,
                battery=None,
                charging=None,
                online=False,
                last_update=time.time(),
                error=f"读取失败：{exc}",
            )

    # --- helpers ----------------------------------------------------------
    def _error(self, message: str, *, keep_last: DeviceStatus | None = None) -> DeviceStatus:
        status = DeviceStatus(
            name=self.display_name,
            brand=self.brand,
            online=False,
            last_update=time.time(),
            error=message,
        )
        if keep_last is not None:
            status.battery = keep_last.battery
            status.charging = keep_last.charging
        return status
