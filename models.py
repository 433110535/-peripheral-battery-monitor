"""Shared data model for every device adapter."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class DeviceStatus:
    """Unified status for a single peripheral.

    All adapters return this, so the UI never needs to know which brand it is
    looking at.
    """

    name: str                     # device name, e.g. "雷蛇鼠标"
    brand: str                    # "Razer" / "HyperX"
    battery: int | None = None    # 0-100, None when unreadable
    charging: bool | None = None  # True/False, None when unknown
    online: bool = False          # is the device reachable right now
    last_update: float = field(default_factory=time.time)
    error: str | None = None      # human-readable problem, if any

    def is_low_battery(self, threshold: int = 20) -> bool:
        return self.battery is not None and self.battery < threshold
