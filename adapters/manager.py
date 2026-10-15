"""DeviceManager -- run every adapter in its own thread and fan out statuses.

This layer is intentionally *not* Qt: it is plain threading plus a callback, so
it can be exercised from a console script (see ``tools/check_manager.py``). The
GUI wires the callback to a Qt signal, which Qt then marshals onto the GUI
thread automatically.

Each adapter gets:
  * its own thread, so a slow/unplugged device never blocks the others;
  * its own polling interval (Razer 5 s, HyperX 15 s);
  * early wake-ups when its source changed (watchdog) or when stopping.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable

from adapters.base import BaseAdapter
from adapters.hyperx import HyperXAdapter
from adapters.razer import RazerAdapter
from models import DeviceStatus

StatusCallback = Callable[[DeviceStatus], None]

# How long a wait slice is before the stop flag is re-checked. Keeps shutdown
# snappy without busy-waiting.
_WAIT_SLICE = 0.25


class DeviceManager:
    def __init__(self, config, logger: logging.Logger | None = None,
                 on_update: StatusCallback | None = None,
                 adapters: Iterable[BaseAdapter] | None = None) -> None:
        self.config = config
        self.log = logger or logging.getLogger("DeviceManager")
        self.on_update = on_update

        self._adapters: list[BaseAdapter] = list(adapters) if adapters else [
            RazerAdapter(config, self.log),
            HyperXAdapter(config, self.log),
        ]
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._latest: dict[str, DeviceStatus] = {}

    # --- introspection ----------------------------------------------------
    @property
    def adapters(self) -> list[BaseAdapter]:
        return list(self._adapters)

    def latest(self) -> dict[str, DeviceStatus]:
        """Snapshot of the most recent status per brand."""
        with self._lock:
            return dict(self._latest)

    def restart_adapter(self, adapter: BaseAdapter) -> None:
        """Drop cached state so the next poll reconnects (e.g. after replug)."""
        with self._lock:
            self._latest.pop(adapter.brand, None)
        adapter.wake()

    # --- lifecycle --------------------------------------------------------
    def start(self) -> None:
        if self._threads:
            return
        self._stop.clear()
        for adapter in self._adapters:
            try:
                adapter.start()
            except Exception:  # noqa: BLE001
                self.log.exception("adapter %s failed to start", adapter.brand)
            thread = threading.Thread(
                target=self._loop, args=(adapter,),
                name=f"adapter-{adapter.brand}", daemon=True)
            thread.start()
            self._threads.append(thread)
        self.log.info("DeviceManager started with %d adapters",
                      len(self._adapters))

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        for adapter in self._adapters:
            try:
                adapter.stop()
            except Exception:  # noqa: BLE001
                self.log.exception("adapter %s failed to stop", adapter.brand)
            adapter.wake()  # break any in-flight wait immediately
        for thread in self._threads:
            thread.join(timeout=timeout)
        self._threads.clear()
        self.log.info("DeviceManager stopped")

    def poll_now(self) -> None:
        """Ask every adapter to refresh on the next loop turn."""
        for adapter in self._adapters:
            adapter.wake()

    # --- worker -----------------------------------------------------------
    def _loop(self, adapter: BaseAdapter) -> None:
        while not self._stop.is_set():
            # Clear before reading: a change that happens *during* the read then
            # still wakes the following wait (and we avoid a double poll when a
            # startup event was already pending).
            adapter.change_event.clear()
            try:
                status = adapter.safe_read()
            except Exception:  # noqa: BLE001 - safe_read shouldn't raise, be safe
                self.log.exception("unexpected error polling %s", adapter.brand)
                status = DeviceStatus(
                    name=adapter.display_name, brand=adapter.brand,
                    online=False, last_update=time.time(),
                    error="内部错误")
            self._publish(adapter, status)
            self._wait(adapter)
        self.log.debug("%s loop exited", adapter.brand)

    def _publish(self, adapter: BaseAdapter, status: DeviceStatus) -> None:
        with self._lock:
            self._latest[adapter.brand] = status
        if self.on_update is not None:
            try:
                self.on_update(status)
            except Exception:  # noqa: BLE001
                self.log.exception("on_update callback failed")

    def _wait(self, adapter: BaseAdapter) -> None:
        """Wait for the interval, waking early on change or stop."""
        deadline = time.monotonic() + adapter.interval
        while not self._stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            if adapter.change_event.wait(min(remaining, _WAIT_SLICE)):
                return
