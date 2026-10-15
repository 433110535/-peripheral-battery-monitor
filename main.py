"""Peripheral Battery Monitor -- entry point.

    python main.py            # normal start (window + tray)
    python main.py --tray     # start hidden in the tray
    python main.py --debug    # verbose logging

Wiring:
    DeviceManager (worker threads)  ->  StatusBridge.updated  ->  window / tray
The manager is plain Python; the bridge turns its callback into a Qt signal so
every status hop lands on the GUI thread automatically.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

import config
from adapters.manager import DeviceManager
from ui.main_window import MainWindow
from ui.tray import Tray


class StatusBridge(QObject):
    """Turns DeviceManager's plain callback into a thread-safe Qt signal."""

    updated = Signal(object)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    debug = "--debug" in argv
    force_tray = "--tray" in argv

    log = config.setup_logging(debug=debug)
    cfg = config.load()
    config.save_default()

    if force_tray:
        cfg.ui.start_minimized = True

    app = QApplication(argv)
    app.setApplicationName("PeripheralBattery")
    app.setApplicationDisplayName("外设电量监控")
    # The tray keeps the app alive even when the window is hidden.
    app.setQuitOnLastWindowClosed(False)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        log.warning("no system tray available on this system")

    bridge = StatusBridge()
    manager = DeviceManager(cfg, log, on_update=bridge.updated.emit)

    window = MainWindow(cfg)
    tray = Tray(cfg)

    bridge.updated.connect(window.on_status)
    bridge.updated.connect(tray.on_status)

    tray.toggle_window.connect(window.toggle_visible)
    tray.refresh_requested.connect(manager.poll_now)
    tray.quit_requested.connect(app.quit)
    window.refresh_requested.connect(manager.poll_now)

    def on_quit() -> None:
        log.info("shutting down")
        manager.stop()

    app.aboutToQuit.connect(on_quit)

    tray.show()
    if cfg.ui.start_minimized:
        tray.showMessage("外设电量监控", "已在托盘后台运行，点击图标查看。",
                         QSystemTrayIcon.Information, 3000)
    else:
        window.show()

    manager.start()
    log.info("started (minimized=%s)", cfg.ui.start_minimized)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
