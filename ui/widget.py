"""Desktop widget: a small, always-on-top frosted-glass card.

The "mini" layout the user picked -- one compact row per device:

    ● Razer Viper V3 Pro          62%
    ▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░
    ● HyperX 耳机                 91%
    ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░

It is a bare QWidget: no title bar, no taskbar button, draggable from anywhere,
snaps to the screen edges when dropped near one, and remembers where it was put
(``config.json`` -> ``ui.widget_x`` / ``ui.widget_y``).

The glass surface is painted here rather than handed to the OS. Windows' own
acrylic material (``SetWindowCompositionAttribute``) was measured on this
machine and rejected: it renders a flat, near-white surface whose lightness
comes from the *wallpaper*, not from what is behind the card, so it hides the
desktop instead of showing it -- and it makes dragging stutter. A hand-painted
veil is predictable on every wallpaper and costs nothing.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal, Slot
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QGuiApplication,
                           QLinearGradient, QPainter, QPainterPath, QPen)
from PySide6.QtWidgets import QMenu, QWidget

# --- geometry (logical pixels) ----------------------------------------------
_CARD_W = 236         # the visible glass card
_MARGIN = 8           # transparent rim around it, used by the drop shadow
_PAD_X = 15           # padding inside the card
_PAD_Y = 13
_LINE_H = 17          # device name + percentage
_BAR_GAP = 6
_BAR_H = 4
_ROW_GAP = 11
_RADIUS = 16

_DOT = 8
_DOT_GAP = 7
_PCT_GAP = 8          # smallest gap allowed between elided name and percentage

_SNAP_MARGIN = 16     # gap left against a screen edge after snapping
_SNAP_DISTANCE = 26   # how close to an edge a drop has to be to snap

# --- palette ----------------------------------------------------------------
_COL_TEXT = "#16202c"
_COL_MUTED = "#5b6675"
_COL_OK = "#22c55e"
_COL_LOW = "#ef4444"
_COL_CHARGING = "#0ea5e9"
_COL_FAINT = "#9ca3af"
_COL_TRACK = QColor(22, 32, 44, 34)       # translucent, the glass shows through
_COL_EDGE = QColor(255, 255, 255, 205)    # the bright hairline of a glass edge
_COL_SHADOW = (15, 23, 42)                # shadow colour, painted at low alpha

# The milky veil: brighter where the light would fall, softer towards the
# bottom. This is what makes the card read as frosted rather than as a pane.
_VEIL_TOP = 214       # 0-255 white
_VEIL_BOTTOM = 168


class DesktopWidget(QWidget):
    """The small always-on-top battery card."""

    open_window_requested = Signal()
    refresh_requested = Signal()
    hide_requested = Signal()
    quit_requested = Signal()
    always_on_top_changed = Signal(bool)
    position_changed = Signal(int, int)

    def __init__(self, config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self._threshold = config.ui.low_battery_threshold
        self._always_on_top = bool(getattr(config.ui, "widget_always_on_top", True))

        self._latest: dict[str, object] = {}
        self._order: list[str] = []
        self._drag_offset: QPoint | None = None

        self._name_font = QFont()
        self._name_font.setPixelSize(13)
        self._name_font.setWeight(QFont.Weight.DemiBold)
        self._pct_font = QFont()
        self._pct_font.setPixelSize(15)
        self._pct_font.setWeight(QFont.Weight.Bold)
        self._fm_name = QFontMetrics(self._name_font)
        self._fm_pct = QFontMetrics(self._pct_font)

        flags = Qt.FramelessWindowHint | Qt.Tool
        if self._always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowTitle("外设电量")
        self.setToolTip("外设电量监控：正在读取…")

        self._recalc_size()

    # --- data ---------------------------------------------------------------
    @Slot(object)
    def on_status(self, status) -> None:
        if status.brand not in self._latest:
            self._order.append(status.brand)
        self._latest[status.brand] = status
        self._recalc_size()
        self._update_tooltip()
        self.update()

    def _update_tooltip(self) -> None:
        parts = []
        for brand in self._order:
            status = self._latest[brand]
            battery = "--" if status.battery is None else f"{status.battery}%"
            if status.charging:
                battery += " 充电中"
            elif not status.online:
                battery += " 未连接"
            parts.append(f"{status.name or brand} {battery}")
        self.setToolTip(" ｜ ".join(parts) or "外设电量监控")

    # --- size / placement ---------------------------------------------------
    def _row_count(self) -> int:
        # Before the first status arrives, reserve two rows: this app watches
        # two devices, and growing later would push the card off a screen edge.
        return len(self._order) or 2

    def _content_height(self, rows: int) -> int:
        block = _LINE_H + _BAR_GAP + _BAR_H
        return _PAD_Y * 2 + rows * block + (rows - 1) * _ROW_GAP

    def _recalc_size(self) -> None:
        width = _CARD_W + _MARGIN * 2
        height = self._content_height(self._row_count()) + _MARGIN * 2
        if self.width() != width or self.height() != height:
            self.setFixedSize(width, height)
            self.update()

    def _default_position(self) -> tuple[int, int]:
        """Bottom-right corner of the primary screen."""
        screen = QGuiApplication.primaryScreen()
        area = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        return (area.x() + area.width() - _SNAP_MARGIN - _MARGIN - _CARD_W,
                area.y() + area.height() - _SNAP_MARGIN - _MARGIN
                - self._content_height(self._row_count()))

    def _clamp_to_screen(self, x: int, y: int) -> tuple[int, int]:
        """Keep the card reachable, even if the monitor it lived on is gone."""
        centre = QPoint(x + self.width() // 2, y + self.height() // 2)
        screen = QGuiApplication.screenAt(centre) or QGuiApplication.primaryScreen()
        if screen is None:
            return x, y
        area = screen.availableGeometry()
        x = max(area.x(), min(x, area.x() + area.width() - self.width()))
        y = max(area.y(), min(y, area.y() + area.height() - self.height()))
        return x, y

    def show_at_saved_position(self) -> None:
        self._recalc_size()
        x, y = self.config.ui.widget_x, self.config.ui.widget_y
        if x is None or y is None:
            x, y = self._default_position()
        self.move(*self._clamp_to_screen(int(x), int(y)))
        self.show()
        self.raise_()

    def _snap_to_edges(self) -> None:
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        # snap the visible card, not the transparent shadow rim
        cx, cy = self.x() + _MARGIN, self.y() + _MARGIN
        card_h = self.height() - _MARGIN * 2

        if abs(cx - area.x()) <= _SNAP_DISTANCE:
            cx = area.x() + _SNAP_MARGIN
        right = area.x() + area.width() - _CARD_W
        if abs(right - cx) <= _SNAP_DISTANCE:
            cx = right - _SNAP_MARGIN

        if abs(cy - area.y()) <= _SNAP_DISTANCE:
            cy = area.y() + _SNAP_MARGIN
        bottom = area.y() + area.height() - card_h
        if abs(bottom - cy) <= _SNAP_DISTANCE:
            cy = bottom - _SNAP_MARGIN

        new_x, new_y = cx - _MARGIN, cy - _MARGIN
        if (new_x, new_y) != (self.x(), self.y()):
            self.move(new_x, new_y)

    # --- interaction --------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if event.button() == Qt.LeftButton:
            self._drag_offset = (event.globalPosition().toPoint()
                                 - self.frameGeometry().topLeft())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self._drag_offset is not None:
            self._drag_offset = None
            self._snap_to_edges()
            self.position_changed.emit(self.x(), self.y())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.open_window_requested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = QMenu(self)
        menu.addAction("打开主窗口", self.open_window_requested.emit)
        menu.addAction("立即刷新", self.refresh_requested.emit)
        menu.addSeparator()
        top = menu.addAction("始终置顶")
        top.setCheckable(True)
        top.setChecked(self._always_on_top)
        top.toggled.connect(self.set_always_on_top)
        menu.addAction("隐藏小插件", self.hide_requested.emit)
        menu.addSeparator()
        menu.addAction("退出", self.quit_requested.emit)
        menu.exec(event.globalPos())

    def set_always_on_top(self, on: bool) -> None:
        on = bool(on)
        if on == self._always_on_top:
            return
        self._always_on_top = on
        self.setWindowFlag(Qt.WindowStaysOnTopHint, on)
        self.show()            # a flag change hides the window, so show it again
        self.always_on_top_changed.emit(on)

    # --- painting -----------------------------------------------------------
    def _state_colours(self, status) -> tuple[str, str]:
        if status.battery is None or not status.online:
            return _COL_FAINT, _COL_FAINT
        if status.battery < self._threshold:
            return _COL_LOW, _COL_LOW
        if status.charging:
            return _COL_CHARGING, _COL_CHARGING
        return _COL_OK, _COL_OK

    def _percent_colour(self, status) -> str:
        if status.battery is None or not status.online:
            return _COL_FAINT
        if status.battery < self._threshold:
            return _COL_LOW
        if status.charging:
            return _COL_CHARGING
        return _COL_TEXT

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        try:
            self._paint_glass(painter)
            if not self._order:
                self._paint_placeholder(painter)
                return
            y = _MARGIN + _PAD_Y
            for brand in self._order:
                self._paint_row(painter, y, self._latest[brand])
                y += _LINE_H + _BAR_GAP + _BAR_H + _ROW_GAP
        finally:
            painter.end()

    def _paint_glass(self, painter: QPainter) -> None:
        card = QRectF(self.rect()).adjusted(_MARGIN, _MARGIN, -_MARGIN, -_MARGIN)

        # --- drop shadow: a few rounded rects fading outwards (cheap and smooth
        # enough at this size, and it never leaves the transparent rim)
        painter.setPen(Qt.NoPen)
        for step in range(_MARGIN, 0, -1):
            fade = 1.0 - (step / float(_MARGIN))
            alpha = int(30 * fade * fade)
            if alpha <= 0:
                continue
            painter.setBrush(QColor(*_COL_SHADOW, alpha))
            painter.drawRoundedRect(
                card.adjusted(-step, -step * 0.55, step, step * 1.45),
                _RADIUS + step, _RADIUS + step)

        # --- the milky veil
        path = QPainterPath()
        path.addRoundedRect(card, _RADIUS, _RADIUS)
        veil = QLinearGradient(card.topLeft(), card.bottomLeft())
        veil.setColorAt(0.0, QColor(255, 255, 255, _VEIL_TOP))
        veil.setColorAt(1.0, QColor(255, 255, 255, _VEIL_BOTTOM))
        painter.setBrush(veil)
        painter.drawPath(path)

        # --- the bright edge that reads as "glass"
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(_COL_EDGE, 1.0))
        painter.drawPath(path)

    def _paint_placeholder(self, painter: QPainter) -> None:
        painter.setFont(self._name_font)
        painter.setPen(QColor(_COL_MUTED))
        card = self.rect().adjusted(_MARGIN, _MARGIN, -_MARGIN, -_MARGIN)
        painter.drawText(card, Qt.AlignCenter, "正在读取…")

    def _paint_row(self, painter: QPainter, y: int, status) -> None:
        dot_colour, bar_colour = self._state_colours(status)
        pct_text = "--" if status.battery is None else f"{status.battery}%"

        left = _MARGIN + _PAD_X
        inner_w = _CARD_W - _PAD_X * 2
        pct_w = self._fm_pct.horizontalAdvance(pct_text)
        text_x = left + _DOT + _DOT_GAP
        name_w = max(24, inner_w - _DOT - _DOT_GAP - pct_w - _PCT_GAP)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(dot_colour))
        painter.drawEllipse(QRectF(left, y + (_LINE_H - _DOT) / 2.0, _DOT, _DOT))

        painter.setFont(self._name_font)
        painter.setPen(QColor(_COL_TEXT))
        name = self._fm_name.elidedText(status.name or status.brand,
                                        Qt.ElideRight, int(name_w))
        painter.drawText(QRectF(text_x, y, name_w, _LINE_H),
                         Qt.AlignLeft | Qt.AlignVCenter, name)

        painter.setFont(self._pct_font)
        painter.setPen(QColor(self._percent_colour(status)))
        painter.drawText(QRectF(left + inner_w - pct_w, y, pct_w, _LINE_H),
                         Qt.AlignRight | Qt.AlignVCenter, pct_text)

        bar_y = y + _LINE_H + _BAR_GAP
        painter.setPen(Qt.NoPen)
        painter.setBrush(_COL_TRACK)
        painter.drawRoundedRect(QRectF(left, bar_y, inner_w, _BAR_H),
                                _BAR_H / 2, _BAR_H / 2)
        if status.battery:
            ratio = max(0.0, min(1.0, status.battery / 100.0))
            if ratio > 0:
                painter.setBrush(QColor(bar_colour))
                painter.drawRoundedRect(
                    QRectF(left, bar_y, inner_w * ratio, _BAR_H),
                    _BAR_H / 2, _BAR_H / 2)
