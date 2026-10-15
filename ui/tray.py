"""System tray icon: battery glyph + context menu + tooltip.

The icon draws the *lowest* battery among the online devices (so a problem is
visible at a glance); the tooltip and the menu list both devices in full.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal, Slot
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap, QPolygonF
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

_COL_OK = "#22c55e"
_COL_LOW = "#ef4444"
_COL_CHARGE = "#38bdf8"
_COL_GREY = "#9ca3af"
_COL_BODY = "#e5e7eb"


def _icon_colour(percent: int | None, charging: bool | None,
                 low_threshold: int) -> str:
    if percent is None:
        return _COL_GREY
    if charging:
        return _COL_CHARGE
    if percent < low_threshold:
        return _COL_LOW
    return _COL_OK


def make_battery_icon(percent: int | None, charging: bool | None,
                      low_threshold: int = 20, size: int = 64) -> QIcon:
    """A battery outline with a proportional fill, drawn at ``size`` px."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)

    margin = size * 0.08
    nub_w = size * 0.08
    body = QRectF(margin, size * 0.26, size - 2 * margin - nub_w, size * 0.48)
    nub = QRectF(body.right() + size * 0.02, size * 0.40, nub_w, size * 0.20)
    radius = size * 0.10

    # outline
    pen = painter.pen()
    pen.setColor(QColor(_COL_BODY))
    pen.setWidthF(max(2.0, size * 0.06))
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawRoundedRect(body, radius, radius)
    painter.drawRoundedRect(nub, radius / 2, radius / 2)

    # fill
    fill_colour = QColor(_icon_colour(percent, charging, low_threshold))
    if percent is not None:
        inner = body.adjusted(size * 0.06, size * 0.06, -size * 0.06, -size * 0.06)
        ratio = max(0.0, min(1.0, percent / 100.0))
        if ratio > 0:
            filled = QRectF(inner.left(), inner.top(),
                            inner.width() * ratio, inner.height())
            painter.setPen(Qt.NoPen)
            painter.setBrush(fill_colour)
            painter.drawRoundedRect(filled, radius / 2, radius / 2)
    else:
        painter.setPen(Qt.NoPen)
        painter.setBrush(fill_colour)
        inner = body.adjusted(size * 0.06, size * 0.06, -size * 0.06, -size * 0.06)
        painter.drawRoundedRect(inner, radius / 2, radius / 2)

    # lightning bolt while charging
    if charging:
        bolt = QPolygonF([
            QPointF(size * 0.50, size * 0.14),
            QPointF(size * 0.34, size * 0.52),
            QPointF(size * 0.47, size * 0.52),
            QPointF(size * 0.40, size * 0.88),
            QPointF(size * 0.62, size * 0.44),
            QPointF(size * 0.48, size * 0.44),
        ])
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#0ea5e9"))
        painter.drawPolygon(bolt)

    painter.end()
    return QIcon(pixmap)


class Tray(QSystemTrayIcon):
    toggle_window = Signal()
    refresh_requested = Signal()
    quit_requested = Signal()

    def __init__(self, config, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self._threshold = config.ui.low_battery_threshold
        self._latest: dict[str, object] = {}

        self._menu = QMenu()
        self._device_actions: dict[str, QAction] = {}
        # NOTE: parented to the tray, not the menu, so QMenu.clear() (used when
        # rebuilding the device list) does not delete them.
        self._toggle_action = QAction("显示/隐藏主窗口", self)
        self._toggle_action.triggered.connect(self.toggle_window.emit)
        self._refresh_action = QAction("立即刷新", self)
        self._refresh_action.triggered.connect(self.refresh_requested.emit)
        self._quit_action = QAction("退出", self)
        self._quit_action.triggered.connect(self.quit_requested.emit)

        self._rebuild_menu()
        self.setContextMenu(self._menu)

        self.setIcon(make_battery_icon(None, None, self._threshold))
        self.setToolTip("外设电量监控：正在读取…")

        self.activated.connect(self._on_activated)

    # ------------------------------------------------------------------
    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.toggle_window.emit()

    @Slot(object)
    def on_status(self, status) -> None:
        self._latest[status.brand] = status
        self._rebuild_menu()
        self._update_icon()
        self._update_tooltip()

    def _rebuild_menu(self) -> None:
        """Rebuild the whole menu: device lines on top, commands below."""
        self._menu.clear()
        self._device_actions.clear()
        for brand, status in self._latest.items():
            action = QAction(self._device_line(status), self._menu)
            action.setEnabled(False)
            self._menu.addAction(action)
            self._device_actions[brand] = action
        if self._latest:
            self._menu.addSeparator()
        self._menu.addAction(self._toggle_action)
        self._menu.addAction(self._refresh_action)
        self._menu.addSeparator()
        self._menu.addAction(self._quit_action)

    def _device_line(self, status) -> str:
        battery = "--" if status.battery is None else f"{status.battery}%"
        bits = [f"{status.name or status.brand}：{battery}"]
        if status.charging:
            bits.append("充电中")
        if not status.online:
            bits.append("未连接")
        return "  ".join(bits)

    def _update_tooltip(self) -> None:
        parts = []
        for status in self._latest.values():
            battery = "--" if status.battery is None else f"{status.battery}%"
            if status.charging:
                battery += " 充电中"
            elif not status.online:
                battery += " 未连接"
            parts.append(f"{status.name or status.brand} {battery}")
        # Windows caps tooltips at ~63 chars.
        self.setToolTip(" ｜ ".join(parts)[:120] or "外设电量监控")

    def _update_icon(self) -> None:
        best = None
        for status in self._latest.values():
            if status.battery is None:
                continue
            if best is None or status.battery < best.battery:
                best = status
        if best is None:
            self.setIcon(make_battery_icon(None, None, self._threshold))
        else:
            self.setIcon(make_battery_icon(best.battery, best.charging,
                                           self._threshold))
