"""Peripheral Battery Monitor -- entry point.

    python main.py            # main window + desktop widget + tray
    python main.py --tray     # start with no main window (used by autostart)
    python main.py --widget   # desktop widget only, no main window
    python main.py --debug    # verbose logging

Wiring:
    DeviceManager (worker threads)  ->  StatusBridge.updated  ->  window / widget / tray
The manager is plain Python; the bridge turns its callback into a Qt signal so
every status hop lands on the GUI thread automatically. All three surfaces are
fed from that one signal, so parking the desktop widget costs nothing extra.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

import config
from adapters.manager import DeviceManager
from ui.main_window import MainWindow
from ui.tray import Tray
from ui.widget import DesktopWidget


class StatusBridge(QObject):
    """Turns DeviceManager's plain callback into a thread-safe Qt signal."""

    updated = Signal(object)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    debug = "--debug" in argv
    force_tray = "--tray" in argv
    force_widget = "--widget" in argv

    log = config.setup_logging(debug=debug)
    cfg = config.load()
    config.save_default()

    if force_tray:
        cfg.ui.start_minimized = True

    app = QApplication(argv)
    app.setApplicationName("PeripheralBattery")
    app.setApplicationDisplayName("外设电量监控")
    # The tray (and the desktop widget) keep the app alive with no window open.
    app.setQuitOnLastWindowClosed(False)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        log.warning("no system tray available on this system")

    bridge = StatusBridge()
    manager = DeviceManager(cfg, log, on_update=bridge.updated.emit)

    window = MainWindow(cfg)
    tray = Tray(cfg)
    widget = DesktopWidget(cfg)

    bridge.updated.connect(window.on_status)
    bridge.updated.connect(widget.on_status)
    bridge.updated.connect(tray.on_status)

    tray.toggle_window.connect(window.toggle_visible)
    tray.refresh_requested.connect(manager.poll_now)
    tray.quit_requested.connect(app.quit)
    window.refresh_requested.connect(manager.poll_now)

    # --- desktop widget ---------------------------------------------------
    def show_main_window() -> None:
        window.show()
        window.raise_()
        window.activateWindow()

    def set_widget_visible(visible: bool) -> None:
        """Show/hide the desktop widget and remember the choice."""
        widget.set_user_visible(visible)
        cfg.ui.widget_enabled = bool(visible)
        config.save(cfg)
        tray.set_widget_checked(visible)

    def on_widget_moved(x: int, y: int) -> None:
        cfg.ui.widget_x, cfg.ui.widget_y = int(x), int(y)
        config.save(cfg)

    def on_always_on_top(on: bool) -> None:
        cfg.ui.widget_always_on_top = bool(on)
        config.save(cfg)

    def on_visibility_mode(mode: str) -> None:
        cfg.ui.widget_visibility = mode
        config.save(cfg)
        log.info("desktop widget visibility mode -> %s", mode)

    widget.open_window_requested.connect(show_main_window)
    widget.refresh_requested.connect(manager.poll_now)
    widget.quit_requested.connect(app.quit)
    widget.hide_requested.connect(lambda: set_widget_visible(False))
    widget.position_changed.connect(on_widget_moved)
    widget.always_on_top_changed.connect(on_always_on_top)
    widget.visibility_mode_changed.connect(on_visibility_mode)
    tray.toggle_widget.connect(set_widget_visible)

    def on_quit() -> None:
        log.info("shutting down")
        manager.stop()

    app.aboutToQuit.connect(on_quit)

    tray.show()

    widget_on = bool(cfg.ui.widget_enabled) or force_widget
    show_main = not cfg.ui.start_minimized and not force_widget

    if widget_on:
        widget.set_user_visible(True)
    tray.set_widget_checked(widget_on)

    if show_main:
        window.show()
    elif not widget_on:
        # Nothing visible at all: say where the app went.
        tray.showMessage("外设电量监控", "已在托盘后台运行，点击图标查看。",
                         QSystemTrayIcon.Information, 3000)

    manager.start()
    log.info("started (main_window=%s desktop_widget=%s visibility=%s)",
             show_main, widget_on, cfg.ui.widget_visibility)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
